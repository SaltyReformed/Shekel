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

* it stamped ``paid_at = now()`` UNCONDITIONALLY on entering a settled status
  rather than preserving an existing instant, so an identity re-submit of an
  unchanged status re-dated a settled transfer -- and since plan step E1a that
  day IS the posted ``entry_date``, so it moved the money (finding **N-146**);
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

from datetime import datetime
from typing import Optional, Union

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.state_machine import verify_transition
from app.utils.balance_predicates import settled_status_ids

#: The rows this seam accepts.  ``Transfer`` carries no ``paid_at`` column --
#: a transfer's settle instant lives on its two shadow ``Transaction`` rows --
#: so the timestamp half of the mechanics is skipped for one of the two.  The
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
    paid_at: Optional[datetime] = None,
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
      3. maintain ``paid_at`` (see the *paid_at* arg) -- **transactions only**,
         because ``Transfer`` has no such column: a transfer's settle instant
         lives on its two shadow rows, and the transfer service applies this
         seam to those shadows, so a transfer settle still records its instant.
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
        paid_at: Payment-timestamp policy, read only for a ``Transaction``.
            ``None`` (the default) DERIVES the timestamp from *new_status_id*:
            stamp ``db.func.now()`` on entering a settled status (Paid /
            Received / Settled) that has none yet, **preserve an existing one on
            an idempotent re-settle**, and clear it on entering a non-settled
            status (so a reverted / cancelled / credited row drops its stale
            payment time).  A non-``None`` ``datetime`` is written verbatim
            (carry-forward back-dating, and the transfer ``mark_done`` route's
            explicit instant).

            **The preserving rule is load-bearing and is finding N-146's fix.**
            The transfer service's deleted seam re-stamped ``now()`` on every
            entry into a settled status including an identity re-submit, and
            since plan step E1a a settle's civil day IS the ``entry_date`` its
            postings are filed under -- so editing the notes on a paid transfer
            moved its money to today.  A caller that genuinely means "clear the
            instant while settled" assigns ``paid_at`` itself afterwards, which
            is what ``update_transfer``'s explicit-``paid_at`` branch does; the
            seam needs no separate sentinel for a case with no caller.

    Raises:
        ValidationError: If the transition is illegal for *row*'s workflow
            (propagated from ``verify_transition``).
        TypeError: If *row* is not a status-bearing model (propagated from the
            state machine -- a programming error at the call site).
    """
    verify_transition(row, new_status_id)
    row.status_id = new_status_id

    # paid_at maintenance.  An explicit timestamp wins; otherwise derive from
    # the new status: clear when leaving the settled band, stamp now() on the
    # first entry into it, and leave an existing stamp untouched on a re-settle
    # (so editing a Paid row -- which re-submits its unchanged status_id -- never
    # churns the original payment time).  Skipped whole for a Transfer, which
    # has no such column; its shadows carry the instant and get their own call.
    if isinstance(row, Transaction):
        if paid_at is not None:
            row.paid_at = paid_at
        elif new_status_id not in settled_status_ids():
            row.paid_at = None
        elif row.paid_at is None:
            row.paid_at = db.func.now()

    db.session.expire(row, ["status"])
