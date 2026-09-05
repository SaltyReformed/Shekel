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
gaining a parameter.  A reader that takes this door cannot reach the
un-narrowed answer by forgetting an argument.  A reader that goes ROUND the
door still can: ``resolve`` builds a value with no derived half, because the
pure package cannot fold a balance, and generation
(``recurrence_engine/_plan.py``) reads that value until plan step R7d-c-2 takes
this door -- so the encoding gets stronger with each R7d leaf rather than being
complete here (:mod:`app.services.recurrence._closing` states the same limit).

What it does NOT do
-------------------

It resolves; it decides no policy of its own.  ``None`` back from
:func:`~app.services.loan_recurrence_sync.loan_payment_window` means "no
derived source bounds this definition" -- a transaction template pays into no
account at all, and a transfer into a savings account has no derived stop --
and that is carried through as a :class:`~app.services.recurrence.Closing` with
no derived half rather than translated into some neutral shape.

The one policy it APPLIES is a developer ruling (**R-R56**, 2026-09-04): **a
closing bound the APP writes is read as the cache it is, not as the owner's
word.**  Until plan step R7d-g deletes the stored copy, ten chokepoints write a
loan payment's derived payoff into ``budget.recurrence_rules.end_date`` -- the
authored bound's own column -- and the EDIT form locks the control, so for the
definition :func:`~app.services.loan_recurrence_sync.is_standing_loan_payment`
names, that column is the app's to write.  Composed as authored it would be
ANDed with the fresh derivation, and where the cache is EARLIER (plan ledger
row **D35**'s measured shape: ``2029-01-22`` stored against ``2029-02-22``
derived) the stale date would still bind.  So the door composes
``authored=NEVER_ENDS`` for that definition and the derived stop is the whole
answer -- :func:`authored_closing` is that arm, stated once and read by the
recurrence form's inverted-window refusal as well as by this door (plan step
R7d-f).  A second recurring transfer into the same loan keeps whatever its
owner authored -- for as long as an older active transfer is the loan's
payment; archive that one and the second is promoted, its column is written by
the next chokepoint, and this door reads it as the cache from then on, which
is what the sync will make it.

**Three limits, stated because the schema records who wrote a bound nowhere.**
(1) The predicate answers "does the app write this bound", not "did it write
the value there now".  The generic create form (``POST /transfers``) cannot
lock the Ends control -- the destination is unknown at render -- and
``settle_first_occurrence`` refuses only a bound BEFORE the derived start, so
an owner CAN author a closing bound on a loan-destination transfer there; no
chokepoint runs on that path, so the column holds the owner's word until the
first chokepoint overwrites it with the payoff (the closing-bound twin of the
opening-bound defect plan step R7c-b fixed; plan ledger row **N-512**, which
R7d-f's third leaf closes by REFUSING such a bound at create for a loan with
no standing payment -- rulings **R-R60** and **R-R61**, developer
2026-09-05).  For that window this
door already reads the column as the cache the sync will make it, so the
Recurring row names the payoff while generation still honours the owner's
date.  (2) An ARCHIVED loan payment -- no longer the
account's active transfer -- has the column the app wrote while it was active
read as its owner's bound in the Archived drawer, and a cache EARLIER than the
derived stop still binds that drawer row until plan step R7d-g NULLs it.  R7d-g
must DECIDE archived loan payments rather than sweep them (plan ledger row
**D56**, an OPEN fork: a NULL-every-loan-payment predicate cannot tell an
authored bound from the cache, so D56 asks R7d-g either to scope the migration
or to rule the erasure intended).  (3) **R7d-g must DELETE this arm with the
column**, and not because the arm goes dead: limit (1) means the create form
stores an owner's bound in that column until R7d-f's third leaf refuses it
(**R-R60**); R7d-g stops nine of the ten syncs and must name whether the
tenth still overwrites a create-form bound; if none does, a kept arm would
read that owner's word as the cache forever.  The EDIT control does not
reopen that route: it stays locked for the loan's own payment (ruling
**R-R59** -- archiving is the door to stop early), so nothing an owner can do
on the edit form writes an authored bound into the column this arm reads
around.  It is a fence around the stored copy and not a design, and it leaves
with the copy.

Flask-isolated (``CLAUDE.md`` Architecture): it takes a template and a read
pass and returns plain values, reads no ``request`` / ``session``, opens no
transaction and writes nothing.  **It takes the pass and never builds one** --
the 2026-08-16 ruling that a producer below the route does not call
``BalanceContext.build`` -- so the calendar a rule is resolved against and the
pass its derived stop is resolved in cannot be two values that disagree.
"""

from dataclasses import replace

from app.services.loan_recurrence_sync import (
    is_standing_loan_payment,
    loan_payment_window,
)
from app.services.balance_at import BalanceContext
from app.services.recurrence import (
    NEVER_ENDS,
    Closing,
    EndBound,
    RecurrenceOwner,
    ResolvedRecurrence,
    RuleReading,
    occurrence_placements,
    resolved_recurrence,
)


def authored_closing(
    template: RecurrenceOwner, authored: EndBound, ctx: BalanceContext,
) -> EndBound:
    """Return the closing bound the OWNER stated, as this door reads it.

    **The one statement of ruling R-R56** (developer, 2026-09-04): for the
    loan's STANDING payment -- the definition
    :func:`~app.services.loan_recurrence_sync.is_standing_loan_payment` names
    -- the ``end_date`` column is the chokepoints' cache of the derived payoff
    and not the owner's word, so it is read as no authored bound at all and
    the derived stop is the whole answer.  Every other definition's stored
    bound is its owner's and returns unchanged.

    **Two readers, and their sharing it is the point** (plan step R7d-f).
    :func:`resolved_definition` composes it into the
    :class:`~app.services.recurrence.Closing` every display reads, and
    ``_recurrence_form_refusals.refuse_inverted_window`` grades the pair an
    edit would leave stored against it.  While that refusal read the column
    directly it carried a SKIP for the standing payment -- a loan cleared
    before its first installment stores an inverted pair the owner has no
    control to repair (plan step ``recurrence:R7d-h``) -- and the skip was a
    second spelling of this arm.  Reading the arm's answer makes the skip
    structurally unnecessary: the standing payment's authored half is
    ``NEVER_ENDS``, which inverts against nothing.

    It TAKES the column-read the caller already holds rather than reading the
    two columns itself: the door's is the pure resolver's, the refusal's is
    :func:`~app.services.recurrence.end_bound_from_columns`, and the resolver
    reads the columns through that same function -- so this adds no spelling
    of the column (``CLAUDE.md`` rule 14).

    **R7d-g deletes this function with the column it reads around** (the
    module docstring's third limit): once nothing writes the loan's payoff
    into the authored bound's column, a stored bound is its owner's for every
    definition and there is nothing left to read as a cache.

    Args:
        template: The definition.  See :func:`resolved_definition` for the
            ownership contract.
        authored: What the definition's two bound columns hold, already read
            by the caller.
        ctx: The read pass, for the loan's memoised resolution.

    Returns:
        :data:`~app.services.recurrence.NEVER_ENDS` for the loan's standing
        payment, else *authored* unchanged.
    """
    if is_standing_loan_payment(template, ctx):
        return NEVER_ENDS
    return authored


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
    spelling of the same column.  The one exception is ruling **R-R56** (see
    the module docstring and :func:`authored_closing`): for the definition
    whose closing bound the app itself writes, the stored bound is the
    chokepoints' cache of the derived payoff and is replaced by
    ``NEVER_ENDS``, so only the derived stop binds.

    Args:
        template: The recurring definition -- a ``TransactionTemplate`` or a
            ``TransferTemplate`` (:data:`~app.services.recurrence.
            RecurrenceOwner`), or any object exposing ``recurrence_rule`` and
            ``to_account_id``, which is what the test fixtures build -- plus
            ``user_id`` and ``id`` whenever the destination is a configured
            loan, which :func:`~app.services.loan_recurrence_sync.
            is_standing_loan_payment` reads.
            **Must belong to ``ctx.user_id``**: the caller owns the ownership
            check, as every seam entry this reaches states.  A cross-owner
            pairing is refused one call down by
            :func:`~app.services.recurrence.resolve`, which will not resolve a
            rule against another owner's calendar, and that refusal is reached
            FIRST here: the read pass refuses a foreign loan too
            (``ForeignAccountError`` from ``BalanceContext._memoize_once``,
            plan step X-i4), but only after loading the account, so resolving
            first is the cheaper refusal and the one whose exception names the
            rule.
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
    # ``obligations_aggregator.template_rule``: that module reads THIS door
    # since plan step R7d-e (its expired filter judges the composed closing),
    # so importing it here would be a cycle one step out -- the same "move the
    # leaf" problem plan step R7d-d solved one layer down, recreated one layer
    # up.  The read is one ``getattr`` and the duck-typed contract is the
    # recurrence package's own
    # (:data:`~app.services.recurrence.RecurrenceOwner`).
    rule = getattr(template, "recurrence_rule", None)
    if rule is None:
        return None
    resolved = resolved_recurrence(rule, ctx.calendar())
    if resolved is None:
        return None
    # The occurrence walk is deliberately NOT run first.  ``resolved_recurrence``
    # refuses a rule paired with another owner's calendar, so resolving before
    # the destination is asked about makes a cross-owner pairing raise the
    # rule's own refusal before any account is loaded (the pass would refuse
    # the foreign loan too, one load later).  The resolved value is then HANDED to
    # the resolver, whose EMPTY test needs the definition's first occurrence:
    # this is the one resolution of the rule on the pass (``CLAUDE.md`` rule
    # 14), where a first build had the resolver derive it again on its own.
    derived = loan_payment_window(template, resolved, ctx)
    # Ruling R-R56 (:func:`authored_closing`): the bound the APP writes is the
    # cache, not the owner's word.  Asked unconditionally since plan step
    # R7d-f, because the identity now costs nothing the resolver above has
    # not already paid -- it reads the pass's memoised loan resolution, which
    # ``loan_figures`` just filled (or filled with ``None`` for a savings
    # destination), and a transaction template answers before any lookup.
    # The ``derived is not None`` guard that stood here priced a predicate
    # that re-ran two queries per call (plan ledger row **N-511**).  R7d-g
    # deletes the arm with the column it reads around.
    return replace(
        resolved,
        closing=Closing(
            authored=authored_closing(
                template, resolved.closing.authored, ctx,
            ),
            derived=derived,
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


__all__ = ["authored_closing", "read_definition", "resolved_definition"]
