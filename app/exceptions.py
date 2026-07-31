"""
Shekel Budget App -- Domain-Specific Exceptions

Raised by the service layer, caught and translated to HTTP responses
by the route layer.  Keeps business logic free of Flask concerns.

**TWO of them are translated APPLICATION-wide instead of per route**, and for
one reason: each reports a precondition without which NO surface can answer, so
each has exactly one right answer everywhere and per-surface decisions are the
defect.  :class:`BaselineMissingError` is "this user has no baseline scenario"
(plan step X-v, ruling R-BW); :class:`PayCalendarGapError` is "no pay period
contains this date" (plan step X-x, ruling R-CY).  Both are answered by
:mod:`app.error_handlers`.  Sixteen routes deciding the first separately, and
about fifty branches deciding the second, are the defects those rulings end.
"""


class ShekelError(Exception):
    """Base exception for all Shekel domain errors."""


class NotFoundError(ShekelError):
    """Requested resource does not exist."""


class ValidationError(ShekelError):
    """Input data failed business-rule validation."""


class AuthError(ShekelError):
    """Authentication or authorisation failure."""


class ConflictError(ShekelError):
    """Operation would create a conflicting state (e.g. duplicate)."""


class BaselineMissingError(ShekelError, ValueError):
    """The balance seam was asked for a figure by a user with no baseline scenario.

    Every balance the app produces is scoped to a baseline scenario, so a user
    without one has no balance the app can answer -- not a zero balance, an
    UNANSWERABLE one.  :func:`app.services.balance_at.require_scenario` raises
    this at the seam's door, :func:`app.services.scenario_resolver.require_baseline_scenario`
    raises it one tier down, and ONE handler
    (:func:`app.error_handlers.register_error_handlers`'s ``baseline_missing``)
    turns it into the setup-recovery page for a full request and ``204 No
    Content`` for a safe-method HTMX one -- so the answer is decided in ONE
    place instead of at every surface that reads a balance (plan step X-v,
    ruling R-BW).

    Carries :attr:`user_id`, the user the raise was RESOLVED for.  The handler
    logs it beside the requesting user's id, and they differ only when a caller
    built a context for the wrong user -- the one failure this exception's
    ERROR event exists to diagnose, and the one the requester's id alone cannot
    show (plan step X-v2's adversarial design review).

    **It is a state the application cannot produce**, measured 2026-07-28:
    ``auth_service.register_user`` writes a baseline for every owner, nothing in
    ``app/`` or ``scripts/`` deletes a scenario or clears ``is_baseline``, no
    path promotes a companion to owner, and ``scripts/integrity_check`` asserts
    it as critical check DC-08.  The handler exists because "cannot be produced
    by code" is not "cannot exist in the data", and because a user who somehow
    reaches this state needs the repair button rather than a stack trace.

    **It subclasses ``ValueError`` as well as :class:`ShekelError`** so that the
    seam's long-standing documented contract ("raises ``ValueError`` when the
    context has no baseline") stays literally true for every caller and test
    that relies on it, while the handler can catch this condition and nothing
    else.  Catching bare ``ValueError`` at the application tier would swallow
    every unrelated conversion failure in the request.

    Args:
        message: What happened, naming the repair.
        user_id: The user the raise was resolved for, or ``None`` when the
            raiser does not know it (the seam's context always does).

    Attributes:
        user_id: As above.
    """

    def __init__(self, message: str, user_id: int | None = None) -> None:
        """Store the resolved user id alongside the message."""
        super().__init__(message)
        self.user_id = user_id


class PayCalendarGapError(ShekelError, ValueError):
    """A surface needed the pay period containing a date and there is none.

    The pay calendar's twin of :class:`BaselineMissingError`, and it is the same
    rule for the same reason (plan step X-x, ruling R-CY).  Almost everything the
    app shows is anchored on "the period containing today" -- the grid's window,
    the cockpit's current-balance column, the paycheck breakdown, the net-worth
    trend's Today marker -- so a user whose calendar does not cover today has no
    current figure the app can answer.  Not a zero, not last-known: an
    UNANSWERABLE one.

    :func:`app.services.pay_period_service.require_current_period` raises it and
    ONE handler (:func:`app.error_handlers.register_error_handlers`'s
    ``pay_calendar_gap``) turns it into the setup-recovery page for a full
    request and ``204 No Content`` for a safe-method HTMX one.

    **Unlike the baseline, this state IS reachable, and that is why it matters.**
    Measured 2026-07-31 on a prod-shape clone: a schedule that has lapsed, one
    that opens in the future, or a hole between two periods all produce it, and
    a hole is permanent because the rolling top-up counts periods ending on or
    after today and stops when the target is met.  Before this exception, the
    surfaces that could reach the state answered it about fifty different ways,
    the worst of which substituted :attr:`~app.models.account.Account.current_anchor_balance`
    -- a derived cache -- for a computed balance and moved a rendered net worth
    by ``$3,228.55``.

    Carries :attr:`user_id` and :attr:`as_of` so the ERROR event can say WHICH
    user and WHICH date could not be placed.  The date matters here where it does
    not for the baseline: a request pinned to a historical ``as_of`` can fail
    while today is perfectly well covered, and an event that logged only the user
    could not tell those apart.

    **It subclasses ``ValueError`` as well as :class:`ShekelError`** for the
    reason :class:`BaselineMissingError` does: the handler catches this condition
    and nothing else, while callers documented against ``ValueError`` stay
    correct.

    Args:
        message: What happened, naming the repair.
        user_id: The user the raise was resolved for.
        as_of: The date that could not be placed in a pay period.

    Attributes:
        user_id: As above.
        as_of: As above.
    """

    def __init__(self, message: str, user_id: int | None = None,
                 as_of=None) -> None:
        """Store the resolved user id and the unplaceable date."""
        super().__init__(message)
        self.user_id = user_id
        self.as_of = as_of


class RecurrenceConflict(ShekelError):
    """Recurrence regeneration found overridden or deleted transactions.

    Attributes:
        overridden:  List of transaction IDs with is_override = True.
        deleted:     List of transaction IDs with is_deleted = True.
    """

    def __init__(self, overridden=None, deleted=None):
        self.overridden = overridden or []
        self.deleted = deleted or []
        super().__init__(
            f"Recurrence conflict: {len(self.overridden)} overridden, "
            f"{len(self.deleted)} deleted."
        )


class PayPeriodLocked(ShekelError):
    """A pay-period operation was refused: a target period is hard-locked.

    Raised by truncate / regenerate when the window they would delete or
    rebuild contains a period that may never be removed -- it is
    historical, holds a settled transaction, is an account's balance
    anchor, or is a recurrence rule's origin.  A hard lock is NOT
    overridable (unlike the discard gate); the operation deletes nothing.

    Attributes:
        blocking: A dict mapping each blocking pay-period id to its
            :class:`~app.services.pay_period_admin.PeriodLockReason`.
    """

    def __init__(self, blocking):
        self.blocking = blocking
        super().__init__(
            f"Operation refused: {len(blocking)} pay period(s) are locked "
            f"(historical, settled, an account anchor, or a recurrence "
            f"anchor) and cannot be deleted or rebuilt."
        )


class PayPeriodDiscardRequired(ShekelError):
    """A destructive pay-period op would discard unrecoverable rows.

    Raised by truncate / regenerate when the affected window holds rows
    regeneration cannot reproduce -- hand-entered (no template), manual
    overrides, or deliberately Credit/Cancelled rows -- and the caller has
    not passed ``confirm_discard=True``.  Unlike :class:`PayPeriodLocked`,
    this gate is overridable: the user may confirm and proceed.  The
    operation deletes nothing until confirmed.

    Attributes:
        count: The number of rows that would be discarded.
    """

    def __init__(self, count):
        self.count = count
        super().__init__(
            f"This will permanently discard {count} hand-entered or changed "
            f"item(s) that cannot be regenerated. Confirm to proceed."
        )


class PayPeriodResetBlocked(ShekelError):
    """A full pay-period reset was refused: the user has settled history.

    Raised by ``pay_period_admin.reset_pay_periods`` when the user has at
    least one settled (Paid / Received / Settled), non-deleted
    transaction.  Reset rebuilds the WHOLE schedule -- including the
    account anchor period -- and is a first-time-setup correction only; a
    user whose paychecks have begun settling must use Regenerate (which
    rebuilds only the unlocked future tail) so settled money is never
    rewritten under a new schedule.  The operation changes nothing.

    Attributes:
        settled_count: The number of settled transactions blocking the
            reset.
    """

    def __init__(self, settled_count):
        self.settled_count = settled_count
        super().__init__(
            f"Cannot reset the schedule: you have {settled_count} settled "
            f"transaction(s).  Reset rebuilds your entire schedule and is "
            f"only for first-time setup before any paychecks have settled.  "
            f"Use Regenerate to rebuild your future schedule instead."
        )
