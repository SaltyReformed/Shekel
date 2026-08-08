"""
Shekel Budget App -- Domain-Specific Exceptions

Raised by the service layer, caught and translated to HTTP responses
by the route layer.  Keeps business logic free of Flask concerns.

**One of them is translated APPLICATION-wide instead of per route**:
:class:`BaselineMissingError` is answered by a single handler in
:mod:`app.error_handlers` (plan step X-v, ruling R-BW), because the condition
it reports -- this user has no baseline scenario, so no balance is computable
for them -- has exactly one right answer on every surface, and sixteen routes
deciding it separately is the defect that ruling exists to end.
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


class UndatedSettleError(ShekelError, ValueError):
    """A settled transaction was read for its settle day and carries none.

    A row is settled if and only if it records the civil day its money moved.
    Both facts are written by one statement -- ``status_seam.apply_status_change``
    assigns ``status_id`` and ``settled_on`` together -- so a row that breaks the
    pairing was written around that seam (a bulk ``query.update()``, or a
    fixture constructing the row directly).

    Raised by ``app.utils.balance_predicates.settled_day``, the single accessor
    every balance and posting consumer asks the question through (plan step
    X-f1, ruling R-EC).  It is a REFUSAL rather than a fallback on purpose: the
    day is a stored fact now, so inventing one would put real money on a day
    nothing recorded, and skipping the row would remove money from a balance
    without saying so.

    A ``ValueError`` as well as a ``ShekelError``, mirroring
    :class:`BaselineMissingError` below: the condition is a broken invariant in
    stored data rather than a user input error, and no route translates it --
    reaching a user as a 500 is the correct disposition, because the request
    cannot be answered correctly and answering it wrongly is worse.
    """


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


class RecurrenceCadenceUnsupported(ShekelError):
    """One paycheck must host a recurring row TWICE, and the table cannot.

    A monthly bill is owed monthly whatever the pay cadence (developer ruling,
    2026-08-07), so at a cadence of 30 days or more several of its occurrences
    legitimately fall inside one paycheck -- three months of rent inside one
    90-day pay period.  ``idx_transactions_template_period_scenario`` (and its
    transfer twin) is UNIQUE over ``(template, pay_period, scenario)``, so
    ``budget.transactions`` can hold only ONE of them.

    **The index is keyed on the wrong column, and that is the real defect**
    (plan ledger row D19): it is a generation-idempotency guard, and a
    generated row's identity is its OCCURRENCE, not its paycheck.  Plan step
    R5 re-keys it onto the occurrence and this exception goes with the fix.

    Until then generation REFUSES, naming the template and the dates
    (developer ruling, 2026-08-08).  The two alternatives were measured and
    are worse: writing the rows raises an unhandled ``IntegrityError`` that
    names nothing and rolls back the whole enclosing transaction -- a pay
    schedule extend among them -- and writing only the first silently
    under-budgets the paycheck by every occurrence after it ($3,000 on a
    $1,500 rent at a 90-day cadence).

    Unreachable below a 30-day cadence, because every calendar month then
    holds at least one payday; ``cadence_days`` is user-selectable 1..365
    (``schemas/validation/pay_periods.py``), so it is configuration rather
    than a hypothetical.

    ONE handler answers it for the whole application
    (:func:`app.error_handlers.register_error_handlers`'s
    ``recurrence_cadence_unsupported``), because the eleven call sites that
    can reach generation would otherwise each decide for themselves -- the
    failure mode plan step X-v's ruling R-BW catalogued for
    :class:`BaselineMissingError`.

    **It names the individual DATES, and plan step R4b-2 is what made them
    available.**  The developer's ruling (2026-08-08) said "naming the dates";
    at plan step R4a the engines still answered in PERIODS -- the adapter
    returned one pay period per occurrence and discarded the occurrence itself
    -- so a count was all that could be stated without calling the occurrence
    engine a second time, which would have been a second producer of one
    answer.  Generation now carries the ``(occurrence, period)`` pairs, so the
    dates come from the same walk that found the collision.
    :attr:`occurrence_count` is DERIVED from them rather than passed beside
    them, because a count and its own list are one fact.

    Args:
        template_name: The recurring definition that cannot be generated.
        occurrence_dates: Every occurrence date that falls inside the one
            paycheck, ascending -- as generation walked them.  At least two,
            or there would be nothing to refuse.
        period_start: The paycheck's own payday.
        period_end: The last day the paycheck covers.

    Attributes:
        template_name: As above.
        occurrence_dates: As above, as a tuple.
        period_start: As above.
        period_end: As above.
    """

    def __init__(self, template_name, occurrence_dates, period_start, period_end):
        """Build the message from the facts a user needs to act on."""
        dates = tuple(occurrence_dates)
        listed = ", ".join(day.isoformat() for day in dates)
        super().__init__(
            f"'{template_name}' falls {len(dates)} times ({listed}) inside the "
            f"single pay period {period_start.isoformat()} to "
            f"{period_end.isoformat()}. Shekel budgets one row per recurring "
            f"definition per paycheck, so it cannot yet hold them separately. "
            f"Shorten the pay cadence, or change how often this definition "
            f"repeats."
        )
        self.template_name = template_name
        self.occurrence_dates = dates
        self.period_start = period_start
        self.period_end = period_end

    @property
    def occurrence_count(self) -> int:
        """How many times the definition falls inside the one paycheck.

        Derived from :attr:`occurrence_dates` rather than stored beside it:
        the two are one fact, and a stored count is a cache of its own list.
        """
        return len(self.occurrence_dates)


class RecurrenceWindowError(ShekelError):
    """A generate pass was handed a write window its owner's schedule lacks.

    A broken invariant rather than user input.
    :class:`~app.services.generation_schedule.GenerationSchedule` loads the
    owner's whole schedule itself and takes only the WINDOW from its caller
    (plan step R4b), so the two can disagree in exactly two ways, and both are
    bugs:

    * the caller paired one user's template with another user's pay period --
      every path already ownership-checks before reaching generation, so this
      is a route-layer hole or a probe;
    * the caller passed an UNSAVED period, which has no id to match against a
      schedule read back from the database.  The repopulation paths flush
      before populating (``pay_period_service.generate_pay_periods``), so an
      unsaved period here means a caller skipped that.

    Raised rather than skipped because both alternatives are silent: a window
    period the schedule does not contain simply matches nothing, and the pass
    would report "generated 0 rows" for a template whose rule fires every
    paycheck.  A recurring bill that quietly stops being generated is exactly
    the failure this arc exists to make loud.
    """


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
