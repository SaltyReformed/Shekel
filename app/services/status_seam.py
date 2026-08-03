"""
Shekel Budget App -- Status Seam

The single status-mechanics primitive for BOTH status-bearing rows.
``apply_status_change`` is the ONE place a ``Transaction.status_id`` or a
``Transfer.status_id`` is assigned.  Every status-changing path -- the manual
``mark_done`` branch, the inline PATCH, ``cancel``, ``mark_as_credit`` /
``unmark_credit``, the envelope ``settle_from_entries``, and the transfer
service's mirror onto a transfer and its two shadow rows -- routes through it so
the status mechanics are uniform and impossible to skip.

**It became the only seam at plan step X-aj1** (ruling **R-DN**,
``docs/audits/balance_architecture/README.md``).  ``transfer_service`` used to
carry a SECOND implementation of this same seam for a transfer's rows, and that
duplication is why the ``shekel-transaction-status-bypass`` checker (W9907)
needed a two-module allowlist at all.  **Merging the seams does not by itself
shrink that allowlist**, and saying so here matters because the obvious
inference is wrong: ``transfer_service`` still writes a status through two
CONSTRUCTORS (``_build_shadow`` and ``create_transfer``), which W9907's
born-Projected rule refuses, so its entry survives until plan step X-aj2 replaces
the write door.  What the merge did remove is the duplicate ATTRIBUTE writes --
and three defects the duplicate had and this one did not:

* it stamped the settle instant UNCONDITIONALLY on entering a settled status
  rather than preserving an existing one, so an identity re-submit of an
  unchanged status re-dated a settled transfer -- and since plan step E1a that
  day IS the posted ``entry_date``, so it moved the money (finding **N-146**).
  **The seam alone did not close that class**: plan step X-f1b0 found both
  mark-done routes passing an explicit instant that overrode this seam's
  preservation, re-dating a replayed settle by however long ago it happened
  (finding **N-178**), and removed both;
* it never expired the ``status`` relationship, though both models declare it
  ``lazy="joined"`` -- latent rather than live, since every route commits before
  rendering, but true by accident rather than by construction;
* it mirrored a drifted shadow's status with no transition check at all, which
  ruling **R-DO** replaced with a refusal.

Architecture:
  - A LOW-LEVEL primitive: it depends only on the state machine, the
    settled-status predicate, the session, and the models -- never on the
    higher-level services that call it (``transaction_service``,
    ``credit_workflow``, ``transfer_service``, the route layer, and the loan /
    paycheck settle paths).  Living below its callers is what keeps it free of
    the ``transaction_service <- entry_service <- entry_credit_workflow <-
    credit_workflow`` import cycle: were the seam in ``transaction_service``
    (which imports ``entry_service``), ``credit_workflow`` could not import it
    without closing that cycle.
  - No Flask imports.  Mutates the passed row in place; does NOT flush or
    commit -- the caller owns the session boundary.
"""

from datetime import date, datetime
from typing import Optional, Union

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.state_machine import verify_transition
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import display_today

#: The rows this seam accepts.  ``Transfer`` carries no ``settled_on`` column --
#: a transfer's settle day lives on its two shadow ``Transaction`` rows --
#: so the dating half of the mechanics is skipped for one of the two.  The
#: branch is on the MODEL, never on ``hasattr``: a probe would silently skip the
#: maintenance for any future row that merely spelled the column differently,
#: and this arc has already paid for a ``hasattr``-shaped test -- plan step
#: X-aa's, whose lesson is Section 8's "``hasattr`` on a dataclass is not a
#: test".  (An earlier draft cited ruling R-CQ for that; R-CQ is the classifier
#: RENAME and carries no such lesson.)
StatusBearingRow = Union[Transaction, Transfer]


def apply_status_change(
    row: StatusBearingRow,
    new_status_id: int,
    *,
    settled_on: Optional[date] = None,
) -> None:
    """Apply a status transition -- the single status seam, for either row type.

    The ONE place a ``Transaction.status_id`` or a ``Transfer.status_id`` may be
    assigned.  Every status-changing path -- the manual ``mark_done`` branch,
    the inline PATCH, ``cancel``, ``mark_as_credit`` / ``unmark_credit``, the
    envelope ``transaction_service.settle_from_entries``, and
    ``transfer_service.update_transfer``'s mirror onto a transfer and its two
    shadows -- routes through here so the status mechanics are uniform and
    impossible to skip.

    Does the status MECHANICS only, in order:

      1. ``verify_transition`` -- the state-machine legality gate, which picks
         the workflow from *row*'s own model class; raises ``ValidationError``
         on an illegal move (e.g. Settled -> Projected), which the route layer
         surfaces as a 400.
      2. assign ``status_id``.
      3. maintain ``settled_on`` (see the *settled_on* arg) -- **transactions
         only**, because ``Transfer`` has no such column: a transfer's settle
         day lives on its two shadow rows, and the transfer service applies
         this seam to those shadows, so a transfer settle still records its day.
      4. ``db.session.expire(row, ["status"])`` so a pre-commit reader (a cell
         render, a test assertion) sees the new ``Status`` row, not the stale
         ``lazy="joined"`` one -- the exact trap ``mark_as_credit`` documented
         and handled inline before this seam absorbed it.  Both models declare
         ``status`` as ``lazy="joined"``, so both need it; the transfer path did
         not do this before X-aj1 and held only because every route commits
         before it renders.

    It deliberately does NOT post to the ledger and does NOT flush or commit:
    ledger emission is reconciled once at the END of each handler, after every
    effect field is applied, never at the status flip (Build-Order Step 3,
    Commit 6 -- the same placement ``transfer_service.update_transfer`` uses);
    the caller owns the session boundary.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` whose status changes.  Must
            be session-attached so the ``status`` expire reloads; its
            ``status_id`` is read as the current state for the transition check
            and its CLASS selects the workflow.
        new_status_id: The ``ref.statuses.id`` to move to.
        settled_on: Settle-day policy, read only for a ``Transaction``.
            ``None`` (the default) DERIVES the day from *new_status_id*: stamp
            ``display_today()`` on entering a settled status (Paid / Received /
            Settled) that has none yet, **preserve an existing one on an
            idempotent re-settle**, and clear it on entering a non-settled
            status (so a reverted / cancelled / credited row drops its stale
            settle day).  A non-``None`` ``date`` is written verbatim, and its
            ONE legitimate meaning is "the user typed this day" -- the transfer
            edit door, and the transfer service's pair resolution.

            **The day is the USER's, not the server's** (ruling R-DH (b)).
            ``display_today()`` reads the display timezone, where
            ``date.today()`` would read the process's UTC day and file an
            8pm-Eastern settle under tomorrow.  This is also why the seam no
            longer assigns ``db.func.now()``: that reached PostgreSQL's clock,
            one of the four database-clock reaches finding N-65 had to build
            ``_freeze_db_clock`` to contain, and a Python ``date`` is a value
            the suite's own clock freeze already governs.

            **The preserving rule is load-bearing and is finding N-146's fix.**
            The transfer service's deleted seam re-stamped every entry into a
            settled status including an identity re-submit, and since plan step
            E1a a settle's civil day IS the ``entry_date`` its postings are
            filed under -- so editing the notes on a paid transfer moved its
            money to today.  **Finding N-178 then showed the seam alone is not
            enough**: both mark-done routes passed an explicit instant, which
            overrode this rule, and a replayed POST re-dated a settled transfer
            by however long ago it really settled.  Neither route passes one
            now, so the preserve rule is the only rule.

            **``Paid -> Settled`` is a RE-ENTRY, not a first entry**, and that
            is why preservation matters beyond the edit forms: archiving a
            payment must not move its money to the day it was archived.  That
            transition has zero production rows today (finding N-177, which
            proposes deleting the status), and the rule is pinned by a test
            regardless, because a status with no rows is not a status with no
            transitions.

    Raises:
        ValidationError: If the transition is illegal for *row*'s workflow
            (propagated from ``verify_transition``).
        TypeError: If *row* is not a status-bearing model (propagated from the
            state machine -- a programming error at the call site).
    """
    # A ``datetime`` is REFUSED rather than accepted and truncated, and this is
    # not defensive programming -- it is finding N-179, measured.  ``datetime``
    # subclasses ``date``, so the annotation catches nothing and PostgreSQL
    # coerces the value into the ``DATE`` column on the SESSION clock, which is
    # UTC: an instant at 2026-03-04 04:30 UTC (2026-03-03 23:30 Eastern) stores
    # as 2026-03-04, one day later than the user's civil day.  That is exactly
    # the UTC-vs-display split ruling R-DH (b) exists to delete, reintroduced
    # one layer down and SILENTLY -- a converted suite left 16 sites passing an
    # instant here and 8 of them stayed green, one of them writing a journal
    # entry whose DATE column held ``2026-03-20T13:00:00+00:00``.  The check is
    # ordered before ``verify_transition`` so a bad value cannot mutate the row
    # even on a legal transition.
    if isinstance(settled_on, datetime):
        raise TypeError(
            f"settled_on must be a date, got datetime {settled_on!r}.  A "
            "settle records the CIVIL DAY its money moved, and an instant "
            "handed here is truncated by PostgreSQL on the session clock "
            "(UTC), so an evening-Eastern settle would be filed on the "
            "following day.  Pass the user's civil day -- display_today(), or "
            "the day the bank showed."
        )

    verify_transition(row, new_status_id)
    row.status_id = new_status_id

    # settled_on maintenance.  An explicit day wins; otherwise derive from the
    # new status: clear when leaving the settled band, stamp the user's today on
    # the first entry into it, and leave an existing day untouched on a
    # re-settle (so editing a Paid row -- which re-submits its unchanged
    # status_id -- never churns the day its money moved).  Skipped whole for a
    # Transfer, which has no such column; its shadows carry the day and get
    # their own call.
    if isinstance(row, Transaction):
        if settled_on is not None:
            row.settled_on = settled_on
        elif new_status_id not in settled_status_ids():
            row.settled_on = None
        elif row.settled_on is None:
            row.settled_on = display_today()

    db.session.expire(row, ["status"])
