"""Shekel Budget App -- the ORM half of an append-only table's refusal.

**Three account tables record FACTS that are never edited**, each for the same
reason: a row states what was true at a moment, and saying something else means
saying it again rather than rewriting what was said.

* :class:`~app.models.account.AccountAnchorHistory` -- what a bank showed on a
  day (ruling **R-DH**);
* :class:`~app.models.account_opening.AccountOpening` -- what an account held
  before its records begin (ruling **R-GX**, latest restatement governs);
* :class:`~app.models.loan_anchor_event.LoanAnchorEvent` -- a loan's owed
  balance at a moment (decision D-A).

**This module is the half that gives a programmer error a NAME; it is not the
half that makes the rule true.**  That is
:mod:`app.append_only_infrastructure`, whose database trigger refuses every
actor and every spelling -- the app, a bulk ``query.update()``, a psql session,
a migration.  A SQLAlchemy event listener sees only writes the ORM mediates, so
on its own it would leave open exactly the surface the finding it closes
(**N-287**) names: ``reconcile_service.record_settled_days`` already stamps a
day onto rows with a bulk ``query.update()``, which fires no listener.

**Why keep this half at all, given the trigger refuses the same writes.**  The
trigger raises a ``psycopg2.errors.RaiseException`` naming a trigger, at flush
time, wrapped in a SQLAlchemy ``InternalError``.  This raises a named Shekel
exception, at the call site, saying what to do instead -- and it is the
exception the suite asserts against, so a test of the rule reads as a statement
about the rule rather than about PostgreSQL's error text.  The two are one rule
in two places only in the sense that a CHECK constraint and its form validator
are: the database decides, the application explains.  Ruling **R-HY**.

**Stated ONCE here rather than hand-written per model.**  The three tables
carried three byte-similar listener pairs, which is the shape that drifts: the
loan twin's docstring still described a scope the cash twin had already
outgrown.  A model declares its own exception type -- so a caller can catch the
table it cares about -- and asks for the guards in one line.
"""

from __future__ import annotations

from sqlalchemy import event


class AppendOnlyViolation(RuntimeError):
    """Base of every "this table is append-only" refusal.

    Carried so a caller that means "any append-only table refused me" -- a
    generic write path, a test sweeping several tables -- has one type to catch,
    while each table keeps its own subclass for the callers that mean one table.
    """


def install_append_only_guards(model, error: type[AppendOnlyViolation]) -> None:
    """Refuse every ORM-mediated UPDATE and DELETE on *model*.

    Registers ``before_update`` and ``before_delete`` mapper listeners that
    raise *error*.  Both fire before SQLAlchemy emits the statement, so the
    offending session rolls back cleanly and the traceback names the call site
    rather than a flush deep inside a commit.

    **The DELETE guard does not interfere with disposing of an ACCOUNT.**  All
    three tables carry :class:`~app.models.mixins.AccountScopedMixin`'s
    ``ON DELETE CASCADE`` foreign key, and a cascade is executed by PostgreSQL
    without loading a row into the session, so no listener fires.  What the
    ORM must NOT do is delete those rows itself on the way to deleting the
    account: :class:`~app.models.account.Account` therefore declares
    ``anchor_history`` with ``passive_deletes=True``, which is what leaves the
    disposal to the database action this guard is deliberately blind to.

    Args:
        model: The mapped class to guard.
        error: The exception type to raise.  A subclass of
            :class:`AppendOnlyViolation` so a generic handler can catch the
            class and a specific one can catch the table.

    Returns:
        None.  The listeners are registered on the mapper for the process's
        lifetime, which is what makes the guard unconditional rather than a
        thing a caller can forget to switch on.
    """
    name = model.__name__

    @event.listens_for(model, "before_update")
    def _block_update(_mapper, _connection, target):  # pragma: no cover - raises
        """Refuse an ORM-mediated UPDATE, naming the row and the remedy."""
        raise error(
            f"{name} is append-only; UPDATE rejected for id={target.id!r}. "
            "Record a correction by inserting a new row."
        )

    @event.listens_for(model, "before_delete")
    def _block_delete(_mapper, _connection, target):  # pragma: no cover - raises
        """Refuse an ORM-mediated DELETE, naming the row."""
        raise error(
            f"{name} is append-only; DELETE rejected for id={target.id!r}. "
            "History goes only with its account, through the database's own "
            "CASCADE."
        )
