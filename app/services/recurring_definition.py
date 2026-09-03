"""
Shekel Budget App -- Reading a recurring DEFINITION (plan step R7d-d)

The one door that reads a recurring definition against a read pass, composing
what its RULE means with what its DESTINATION allows.

:mod:`app.services.recurrence` answers the first half and is pure: given a rule
and the owner's pay calendar it resolves the cadence, the first occurrence and
the bound the owner AUTHORED.  It cannot answer the second half, because
deciding whether something outside the rule stops the definition means folding
a loan's balance forward -- the ORM and the balance seam, neither of which that
package may import.  This module is where the two meet.

Why it is a door and not a helper
---------------------------------

**A definition can be stopped by something it did not author, and every
surface that asks "does this still fire" has to honour both stops.**  A
recurring transfer that pays a loan stops when the debt does.  Until plan step
R7d that fact reaches the walk only as a CACHE: ten call sites WRITE the loan's
derived payoff into ``budget.recurrence_rules.end_date``, the authored bound's
own column -- they still do, until R7d-g deletes nine of them -- so one column
holds two facts and every reader is trusting that some earlier write was recent
enough (plan ledger row **D35**).  That is
``CLAUDE.md`` rule 14's stored-and-derived case, and the remedy is to delete a
home rather than keep two in step.

Deleting it leaves five surfaces that each need the conjunction: generation,
this surface's next date, its cadence sentence, the ``/obligations`` and
``/savings`` monthly totals, and the recurrence form's preview.  **Written by
hand in five places that is five chances to drift**, and the drift would be
silent -- a forgotten narrowing admits occurrences against a debt that is gone
and nothing raises.

So the conjunction is not a step a reader performs.  It is a VALUE
(:class:`~app.services.recurrence.Closing`) carried by the resolved recurrence
this door returns, and the walk that already reads that value narrows without
gaining a parameter.  A reader cannot reach the un-narrowed answer by
forgetting an argument, because there is no un-narrowed answer to reach.

What it does NOT do
-------------------

It resolves; it does not decide policy.  ``None`` back from
:func:`~app.services.loan_recurrence_sync.loan_payment_window` means "no
derived source bounds this definition" -- a transaction template pays into no
account at all, and a transfer into a savings account has no derived stop --
and that is carried through as a :class:`~app.services.recurrence.Closing` with
no derived half rather than translated into some neutral shape.

Flask-isolated (``CLAUDE.md`` Architecture): it takes a template and a read
pass and returns plain values, reads no ``request`` / ``session``, opens no
transaction and writes nothing.  **It takes the pass and never builds one** --
the 2026-08-16 ruling that a producer below the route does not call
``BalanceContext.build`` -- so the calendar a rule is resolved against and the
pass its derived stop is resolved in cannot be two values that disagree.
"""

from dataclasses import replace

from app.services.loan_recurrence_sync import loan_payment_window
from app.services.balance_at import BalanceContext
from app.services.recurrence import (
    Closing,
    RecurrenceOwner,
    ResolvedRecurrence,
    RuleReading,
    occurrence_placements,
    resolved_recurrence,
)


def resolved_definition(
    template: RecurrenceOwner, ctx: BalanceContext,
) -> ResolvedRecurrence | None:
    """Return what *template*'s recurrence MEANS, narrowed by its destination.

    The composed read: :func:`~app.services.recurrence.resolved_recurrence`
    for what the rule says, :func:`~app.services.loan_recurrence_sync.
    loan_payment_window` for what the destination allows, and the two held in
    one :class:`~app.services.recurrence.Closing` on the value returned.

    Exposed on its own beside :func:`read_definition` for the callers that
    want the cadence and not its rows -- the Recurring surface's archived
    drawer describes every archived definition and places none -- which is the
    same split :func:`~app.services.recurrence.resolved_recurrence` and
    :func:`~app.services.recurrence.read_rule` already keep one layer down.

    **The narrowing is applied HERE and only here**, so "what stops this
    definition" has one implementation.  ``replace`` rather than a mutation
    because :class:`~app.services.recurrence.ResolvedRecurrence` is frozen, and
    the authored half is carried across from the value the pure resolver
    built rather than re-read off the rule: reading it twice would be a second
    spelling of the same column.

    Args:
        template: The recurring definition -- a ``TransactionTemplate`` or a
            ``TransferTemplate`` (:data:`~app.services.recurrence.
            RecurrenceOwner`), or any object exposing ``recurrence_rule`` and
            ``to_account_id``, which is what the test fixtures build.
            **Must belong to ``ctx.user_id``**: the caller owns the ownership
            check, as every seam entry this reaches states.  A cross-owner
            pairing is refused one call down by
            :func:`~app.services.recurrence.resolve`, which will not resolve a
            rule against another owner's calendar -- and that refusal is
            reached FIRST here, deliberately, because
            :func:`~app.services.loan_recurrence_sync.loan_payment_window`
            records that such a pairing produces a plausible BLENDED answer
            rather than a refusal.
        ctx: The read pass.  Its ``calendar()`` is the schedule the cadence
            resolves against and its ``as_of`` and scenario scope the fold, so
            both halves are measured in one pass.

    Returns:
        The :class:`~app.services.recurrence.ResolvedRecurrence`, or ``None``
        when the definition does not repeat (no rule names it) or the owner's
        schedule holds no pay periods -- the two ``None`` cases
        :func:`~app.services.recurrence.resolved_recurrence` already
        distinguishes for its callers, passed through unchanged.

    Raises:
        RecurrenceResolutionError: The rule cannot be resolved against the
            owner's schedule -- an unmodelled cadence, a domain violation, or
            a rule paired with another owner's pass.
        BaselineMissingError: The definition pays into a configured loan and
            *ctx* has no baseline scenario (ruling **R-R30**), from the seam's
            own ``require_scenario``.  A definition with no loan behind it
            still resolves for such an owner: the not-a-loan answer is reached
            before the scenario guard.
    """
    # ``getattr`` rather than attribute access, and NOT
    # ``obligations_aggregator.template_rule``: plan step R7d-e moves that
    # module's ``has_ended`` onto THIS door, so importing it here would be a
    # cycle one step out -- the same "move the leaf" problem this step just
    # solved one layer down, recreated one layer up.  The read is one
    # ``getattr`` and the duck-typed contract is the recurrence package's own
    # (:data:`~app.services.recurrence.RecurrenceOwner`).
    rule = getattr(template, "recurrence_rule", None)
    if rule is None:
        return None
    resolved = resolved_recurrence(rule, ctx.calendar())
    if resolved is None:
        return None
    # The occurrence walk is deliberately NOT run first.  ``resolved_recurrence``
    # refuses a rule paired with another owner's calendar, so resolving before
    # the destination is asked about is what makes a cross-owner pairing raise
    # rather than reach the loan fold.
    return replace(
        resolved,
        closing=Closing(
            authored=resolved.closing.authored,
            derived=loan_payment_window(template, ctx),
        ),
    )


def read_definition(
    template: RecurrenceOwner, ctx: BalanceContext,
) -> RuleReading:
    """Read *template* against *ctx*, keeping the meaning and the placements.

    :func:`resolved_definition` plus the occurrence walk, which is the
    composition :func:`~app.services.recurrence.read_rule` makes one layer
    down -- and this is that function with the destination's own stop applied,
    so a caller takes this rather than performing the two steps itself.

    **The placements are walked under the composed closing**, because the walk
    reads it off the resolved value.  So a surface's "next date" and its
    cadence sentence are two readings of ONE narrowing and cannot come apart.
    They do not disagree today either -- ``recurring_view._build_section``
    already reads each rule once and derives both from that reading, and an
    adversarial review of this step corrected an earlier sentence here for
    claiming otherwise.  What this preserves is that property through a
    second stop being added, rather than repairing a disagreement that
    existed.

    Args:
        template: The recurring definition.  See :func:`resolved_definition`
            for the ownership contract.
        ctx: The read pass.

    Returns:
        The :class:`~app.services.recurrence.RuleReading`.  Its ``resolved`` is
        ``None`` with no placements for a definition that does not repeat or
        an owner with no pay periods, which is what
        :func:`~app.services.recurrence.read_rule` answers for the same two
        states.

    Raises:
        RecurrenceResolutionError: See :func:`resolved_definition`.
        RecurrenceGenerationError: The resolved value names something the
            occurrence engine cannot walk.  See
            :func:`~app.services.recurrence.rule_occurrences`.
        BaselineMissingError: See :func:`resolved_definition`.
    """
    resolved = resolved_definition(template, ctx)
    if resolved is None:
        return RuleReading(resolved=None, placements=())
    return RuleReading(
        resolved=resolved,
        placements=occurrence_placements(resolved, ctx.calendar()),
    )


__all__ = ["read_definition", "resolved_definition"]
