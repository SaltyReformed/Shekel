"""
Shekel Budget App -- Domain-Specific Exceptions

Raised by the service layer, caught and translated to HTTP responses
by the route layer.  Keeps business logic free of Flask concerns.
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
