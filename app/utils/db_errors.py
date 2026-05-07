"""
Shekel Budget App -- Database Error Helpers

Utilities for inspecting :class:`sqlalchemy.exc.IntegrityError` objects
without resorting to substring matching of the underlying driver's
error message.  String matching is brittle: PostgreSQL's
``UniqueViolation`` text format is documented but not guaranteed to
remain identical across versions, and a constraint name that happens
to appear inside another part of the message would produce a false
positive.

psycopg2 instead exposes the structured PostgreSQL error packet via
``exception.diag.constraint_name`` (and the rest of the
``Diagnostics`` interface).  This module wraps that lookup so the
route layer can answer "did the IntegrityError fire on the named
constraint?" with a single call.

The helper is the load-bearing piece of the C-19 idempotency
backstop: when ``credit_workflow.mark_as_credit`` or
``entry_credit_workflow.sync_entry_payback`` is bypassed by a future
caller and a duplicate CC Payback insert reaches PostgreSQL, the
partial unique index ``uq_transactions_credit_payback_unique``
rejects it, the route layer recognises the constraint name through
this helper, and the user sees idempotent success instead of an HTTP
500.
"""

from sqlalchemy.exc import IntegrityError


def is_unique_violation(exc: IntegrityError, constraint_name: str) -> bool:
    """Return True when the IntegrityError fired on the named constraint.

    The check inspects ``exc.orig.diag.constraint_name``, the
    structured PostgreSQL error field that psycopg2 surfaces verbatim
    from the server's ``ErrorResponse`` packet.  This is exact and
    avoids the false-positive risk of a substring match on the
    free-form error message.

    Args:
        exc: The IntegrityError caught by the calling code.  The
            underlying driver exception is read from ``exc.orig``;
            if absent, the helper conservatively returns ``False``.
        constraint_name: The expected constraint or index name (e.g.
            ``"uq_transactions_credit_payback_unique"``).  Compared
            for exact equality.

    Returns:
        ``True`` if the underlying error reported the named
        constraint, ``False`` otherwise (including the case where
        the driver did not populate ``diag.constraint_name`` -- some
        non-uniqueness violations leave it blank).

    Notes:
        psycopg2 always sets ``diag.constraint_name`` for
        ``UniqueViolation`` errors -- the field is part of the wire
        protocol and the driver does not strip it.  A future swap to
        psycopg3 keeps the same attribute name, so this helper
        continues to work without modification.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    diag = getattr(orig, "diag", None)
    if diag is None:
        return False
    actual = getattr(diag, "constraint_name", None)
    return actual == constraint_name
