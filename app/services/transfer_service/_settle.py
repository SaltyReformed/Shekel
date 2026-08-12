"""
Shekel Budget App -- Transfer Service: what SETTLING a transfer means

The amount half of a settle, for all three rows at once.  Moving a transfer
into the settled band is not just a status change: an auto-derived loan payment
must FREEZE the live payment-date cash it is actually worth, and a figure a
human typed must be told apart from the panel's own prefill echoed back.

**The rule lives here rather than at each door, and that is ruling R-FA applied
to the transfer table.**  It lived in ONE route branch --
``routes/transactions/_shadow_mutations.py``'s ``_mark_done_shadow``, which
called ``loan_payment_service.live_loan_payment_amount`` and handed the answer
to ``update_transfer`` as an ``actual_amount`` -- and FOUR doors can move a
transfer into the settled band:

* the grid's shadow "Mark Paid" (``_mark_done_shadow``), which froze;
* the transfers page's "Mark Done" (``routes/transfers/mutations.mark_done``),
  which did not;
* the transfer full-edit Status dropdown (``_execute_transfer_update``), which
  did not;
* a transaction PATCH landing on a shadow (``_apply_shadow_update``), which did
  not.

That is finding **N-219**'s shape one table over -- a ROUTE holding a money
rule, so one control books a different figure from another for the same
payment.  :func:`app.services.transfer_service.update_transfer` is the one door
every transfer mutation already passes through, so the rule is DISPATCHED there
and no door can be written without it.

**What it costs on production today is `$0.00`, and the reason is worth
stating**: ``budget.loan_payment_settings`` holds ZERO rows, so
``_loan_payment_config`` answers ``(False, 0.00)`` for every transfer template
and :func:`frozen_amount` returns ``None`` everywhere.  All 17 settled transfer
shadows on Checking carry ``actual_amount = NULL``, which is that fact in the
data.  The split opens the first time a loan payment transfer is created
through ``routes/loan/payment_transfer.py``, which is a live route.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Reads ORM rows, returns values.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - PURE: nothing here mutates, flushes or commits.
"""

from decimal import Decimal

from app.models.transaction import Transaction
from app.services import loan_payment_service


def frozen_amount(shadow: Transaction) -> "Decimal | None":
    """Return the live payment-date figure a settle FREEZES, or ``None``.

    The capture-on-settle rule: when the operator settles an auto-derived loan
    payment with one click, the cash recorded is the LIVE payment-date amount
    (P&I + escrow-as-of + extra), not the creation-time estimate the shadow was
    generated with.  Because the frozen cash and the genesis split read the same
    ``escrow_monthly_as_of`` on the shadow's own DUE date, ``cash == split``
    holds by construction rather than by luck.

    ``None`` -- meaning "leave the stored estimate alone" -- for every shadow
    that needs no capture: not a transfer at all, an operator ``is_override``
    (the operator owns that amount), an already-settled shadow, a transfer that
    is not a loan payment (no settings row), a MANUAL payment with no standing
    extra (its stored estimate already IS the cash), or a loan that cannot
    resolve.  ``loan_payment_service.live_loan_payment_amount`` owns that list;
    this is a named seam over it, not a second copy.

    **The ``is_projected`` guard inside makes the freeze ONE-SHOT**, and the
    dispatch in :func:`~app.services.transfer_service.update_transfer` is placed
    to keep it that way: it runs BEFORE the status is applied, so a genuine
    first settle still sees a Projected shadow, while a re-settle of an
    already-Paid one (the ``done -> done`` identity transition a stale tab can
    submit) resolves to ``None`` and never rewrites the frozen figure to a later
    live one that was never paid.

    Args:
        shadow: Either leg of the transfer -- both share the transfer id, the
            pay period and the due date, so either resolves the same figure and
            Transfer Invariant 3 is preserved whichever is passed.

    Returns:
        The ``Decimal`` to freeze, or ``None``.
    """
    return loan_payment_service.live_loan_payment_amount(
        shadow, shadow.scenario_id,
    )


def settle_amount(shadow: Transaction) -> Decimal:
    """Return what settling this transfer would BOOK on *shadow*'s account.

    **The transfer twin of ``transaction_service.settle_amount``, and it exists
    for the same reason**: the reconcile panel must show the figure a tick will
    book, and a panel rendering ``effective_amount`` beside a verb that books
    the freeze is two answers to one money question, one screen apart.  The
    dispatch in :func:`~app.services.transfer_service.update_transfer` resolves
    its own figure through the same two functions, so the displayed figure and
    the booked one cannot drift.

    Args:
        shadow: The leg being offered, still Projected.

    Returns:
        The frozen live figure when there is one, else the row's stored
        ``effective_amount``.
    """
    frozen = frozen_amount(shadow)
    return shadow.effective_amount if frozen is None else frozen


def is_correction(shadow: Transaction, submitted: "Decimal | None") -> bool:
    """Return whether *submitted* is a HUMAN's figure this settle would BOOK.

    The transfer twin of ``transaction_service.is_correction``, and it answers
    the same two questions in one expression: did anybody type a figure, and
    does it DIFFER from what the row would book anyway.  The reconcile panel
    PREFILLS its amount box, so every correctable row on the form posts a figure
    whether the user touched it or not; counting or writing an echoed prefill
    would populate a column that is NULL on all 17 settled transfer shadows in
    production and destroy the only signal that says a human read one off a
    statement.

    Args:
        shadow: The leg being settled, still Projected.
        submitted: The figure a caller supplied, or ``None`` when nobody typed
            one.

    Returns:
        True when the settle will write *submitted* into both legs'
        ``actual_amount``.
    """
    return submitted is not None and submitted != settle_amount(shadow)


def settle_actual(
    shadow: Transaction, submitted: "Decimal | None",
) -> "Decimal | None":
    """Return the figure a settle writes to BOTH legs, or ``None`` for neither.

    The two rules in their precedence order, which is the whole of this
    module's decision:

    1. **A human's correction wins.**  A figure that differs from what the row
       would book anyway is a fact somebody read off a statement, and it beats
       any derivation.
    2. **Otherwise the FREEZE**, when there is one.  Nobody typed a figure, so
       an auto-derived loan payment records what it is live worth on its own
       due date rather than the creation-time escrow its estimate carries.
    3. **Otherwise nothing.**  ``None`` does NOT mean "clear the column" --
       ``update_transfer``'s dispatch distinguishes "this settle has no figure
       of its own" from a caller explicitly clearing one, because those are
       different acts.

    Args:
        shadow: The leg being settled, still Projected.
        submitted: The figure a caller supplied, or ``None``.

    Returns:
        The ``Decimal`` to write, or ``None`` when this settle supplies no
        figure of its own.
    """
    if is_correction(shadow, submitted):
        return submitted
    return frozen_amount(shadow)
