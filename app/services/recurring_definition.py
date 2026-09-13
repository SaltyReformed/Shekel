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
definition :func:`~app.services.balance_at.is_standing_loan_payment`
names, that column is the app's to write.  Composed as authored it would be
ANDed with the fresh derivation, and where the cache is EARLIER (plan ledger
row **D35**'s measured shape: ``2029-01-22`` stored against ``2029-02-22``
derived) the stale date would still bind.  So the door composes
``authored=NEVER_ENDS`` for that definition and the derived stop is the whole
answer -- :func:`~app.services.balance_at.authored_closing` is that arm,
stated once and read by the recurrence form's inverted-window refusal, by
this door (plan step R7d-f) and by the seam's forward plan (plan step
R16-b-2, which moved the arm into the seam because the plan cannot import
this module: ruling **R-R70**).  A second recurring transfer into the same loan keeps whatever its
owner authored -- for as long as an older active transfer is the loan's
payment; archive that one and the second is promoted, its column is written by
the next chokepoint, and this door reads it as the cache from then on, which
is what the sync will make it.

**Three limits, stated because the schema records who wrote a bound nowhere.**
(1) The predicate answers "does the app write this bound", not "did it write
the value there now".  Until plan step R7d-f-3 the generic create form
(``POST /transfers``) could author a closing bound on a loan-destination
transfer -- its server render cannot lock the Ends control, and
``settle_first_occurrence`` refused only a bound BEFORE the derived start --
and no chokepoint runs on that path, so the column held the owner's word
until the first chokepoint overwrote it with the payoff while this door read
it as the cache from the start (plan ledger row **N-512**).  That leaf closed
it at the door (ruling **R-R60**): a stop stated for a loan holding no active
payment is refused, because the definition being created IS that loan's
payment, and a stop stated for a loan that already holds one is a SECOND
transfer's and stays its owner's.  What the limit still names is every row
whose column holds an owner's word this door reads as the cache: the rows
that path wrote before the refusal existed, and a second transfer PROMOTED
by the archiving of the first (above).  Until plan step R7d-f-4 it also
named the UPDATE door's two paths, which R7d-f-3's adversarial review found
(plan ledger row **REC-521**) -- a rule-less transfer into a payment-less
loan given a cadence on the edit form (the identity answers ``False`` for a
template with no rule, so the authoring branch wrote the owner's start AND
stop), and a bounded transfer whose destination is MOVED onto a payment-less
loan (the identity was judged against the stored destination, then the
column moved).  That leaf closed both at the door through the create door's
own reading (``_loan_destination.settle_destination_for_update``,
rulings **R-R76** and **R-R77**).  What the limit names now is the two above
and a THIRD that leaf's adversarial review found and R7d-g owns (plan ledger
row **REC-522**): an ARCHIVED recurring transfer into a loan is editable, is
nobody's standing payment while archived (the identity reads the ACTIVE set),
so its edit form unlocks both bound rows and the update door judges it as any
savings transfer; ``unarchive_transfer_template`` then regenerates it with no
sync, and it is the loan's payment again with an owner's word in the column.
(2) An ARCHIVED
loan payment -- no longer the account's active transfer -- has the column the
app wrote while it was active
read as its owner's bound in the Archived drawer, and a cache EARLIER than the
derived stop still binds that drawer row until plan step R7d-g NULLs it.  R7d-g
must DECIDE archived loan payments rather than sweep them (plan ledger row
**D56**, an OPEN fork: a NULL-every-loan-payment predicate cannot tell an
authored bound from the cache, so D56 asks R7d-g either to scope the migration
or to rule the erasure intended).  (3) **R7d-g must DELETE this arm with the
column**, and not because the arm goes dead: limit (1) names three producers
of an owner's bound in that column that outlive the two doors' refusals
(**R-R60**, **R-R77**); R7d-g stops nine of the ten syncs and must name whether the
tenth still overwrites such a bound; if none does, a kept arm would read
that owner's word as the cache forever.  The EDIT control does not
reopen that route for an ACTIVE definition: it stays locked for the loan's
own payment (ruling **R-R59** -- archiving is the door to stop early), so
nothing an owner can do on an active definition's edit form writes an
authored bound into the column this arm reads around; the archived one is
REC-522's.  It is a fence around the stored copy and not a design, and it
leaves with the copy.

Flask-isolated (``CLAUDE.md`` Architecture): it takes a template and a read
pass and returns plain values, reads no ``request`` / ``session``, opens no
transaction and writes nothing.  **It takes the pass and never builds one** --
the 2026-08-16 ruling that a producer below the route does not call
``BalanceContext.build`` -- so the calendar a rule is resolved against and the
pass its derived stop is resolved in cannot be two values that disagree.
"""

from dataclasses import dataclass, replace

from app.services.loan_recurrence_sync import loan_payment_window
from app.services.balance_at import BalanceContext, authored_closing
from app.services.recurrence import (
    Closing,
    EndBound,
    RecurrenceOwner,
    RecurrenceSpec,
    ResolvedRecurrence,
    RuleReading,
    resolved_spec,
)


@dataclass(frozen=True)
class UnsavedDefinition:
    """A recurring definition a form DESCRIBES and nothing stores yet.

    What the recurrence form's live preview reads through this door (plan
    step R7d-f-2, plan ledger row **REC-515**): the template being previewed
    may not exist -- the create form is showing what saving WOULD produce --
    so there is no row to hand :func:`resolved_definition`, and plan step
    R-F6 deleted the transient ROW the preview used to fabricate for exactly
    that reason.  What the destination-half of the composition actually reads
    off a definition is ONE column
    (:func:`~app.services.recurring_transfer_query.destination_account` reads
    ``to_account_id`` and nothing else), so this is that column and nothing
    else, and :func:`resolved_submission` pairs it with the
    :class:`~app.services.recurrence.RecurrenceSpec` the form states.

    **It carries no ``recurrence_rule`` and no ``id`` on purpose.**  The
    authored half of an unsaved definition's closing is the submission's own
    word by construction -- the form never reads the stored column, so ruling
    **R-R56**'s cache arm (:func:`~app.services.balance_at.authored_closing`)
    has no subject here and :func:`resolved_submission` does not ask it.
    Nothing else about a definition's identity reaches the derived stop.

    Attributes:
        to_account_id: The destination account the form names, or ``None``
            for a transaction template (which pays into no account) and for a
            transfer form that has not stated one.  **Must be the pass
            owner's**: the route resolves the submitted id through the
            ownership gate before building this (the house rule -- 404 for
            missing and for foreign alike), and the pass refuses a foreign
            account a second time when it memoises the loan
            (``ForeignAccountError`` from ``_memoize_once``, plan step X-i4).
    """

    to_account_id: int | None


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

    **The narrowing is applied in :func:`_narrowed` and only there**, so
    "what stops this definition" has one implementation for a stored
    definition and for an unsaved one (:func:`resolved_submission`).  The
    authored half is carried across from the value the pure resolver built
    rather than re-read off the rule: reading it twice would be a second
    spelling of the same column.  The one exception is ruling **R-R56** (see
    the module docstring and :func:`~app.services.balance_at.authored_closing`):
    for the definition
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
            (``ForeignAccountError`` from ``balance_at._memoize._memoize_once``,
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
    # The pass's memo, not a fresh resolution: the forward plan behind the
    # derived stop below walks this same rule to sum the definition's
    # occurrences (plan step R16-b-2), and one pass resolves one rule once.
    resolved = ctx.resolved_recurrence_of(rule)
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
    # Ruling R-R56 (:func:`~app.services.balance_at.authored_closing`): the
    # bound the APP writes is the cache, not the owner's word.  Asked
    # unconditionally since plan step R7d-f, because the identity costs
    # nothing the resolver does not pay anyway -- both read the pass's ONE
    # memoised loan resolution (``resolved_loan``), whichever of the two
    # fills it (the identity does, since R7d-f-2 evaluates it first; the
    # resolver's ``loan_figures`` then reads the memo), and a transaction
    # template answers before any lookup.  The ``derived is not None`` guard
    # that stood here priced a predicate that re-ran two queries per call
    # (plan ledger row **N-511**).  R7d-g deletes the arm with the column it
    # reads around.
    return _narrowed(
        template, resolved,
        authored=authored_closing(template, resolved.closing.authored, ctx),
        ctx=ctx,
    )


def resolved_submission(
    spec: RecurrenceSpec, definition: UnsavedDefinition, ctx: BalanceContext,
) -> ResolvedRecurrence | None:
    """Return what an UNSAVED definition would mean, narrowed by its destination.

    :func:`resolved_definition` for the recurrence form's live preview (plan
    step R7d-f-2, plan ledger row **REC-515**), which has a
    :class:`~app.services.recurrence.RecurrenceSpec` the request states and
    a destination the form names, and no row.  The preview walked the spec's
    own resolution until then, so a transfer into a loan previewed
    occurrences running past the loan's payoff whenever fewer than five
    remained -- on the one surface whose contract is "what saving would
    produce", and while every other reader of a loan payment's schedule had
    taken this door (**R-R34**'s census of the preview as a reader of the
    stored column was inexact: it read neither the column nor the derived
    stop).

    **The same narrowing, applied by the same code.**  Both entries hand
    :func:`_narrowed` a resolved value and what stops it; the ONLY difference
    is the authored half.  A stored definition's is read through ruling
    **R-R56**'s arm because its column may hold the chokepoints' cache; a
    submission's IS the owner's word -- the form's "Ends" controls, or
    :data:`~app.services.recurrence.NEVER_ENDS` when the row is locked and
    posts nothing, which is what the loan's standing payment posts -- so it
    is taken as stated.  Resolved through
    :func:`~app.services.recurrence.resolved_spec`, the producer the pass's
    own memo wraps, rather than through that memo: the memo is keyed by a
    rule's spec and this caller resolves one spec once per request, so there
    is one producer either way and nothing here to collapse.

    Args:
        spec: What the form states, unresolved.
        definition: The destination the form names, as an
            :class:`UnsavedDefinition`.  See its ownership contract.
        ctx: The read pass the ROUTE built (the 2026-08-16 ruling: a producer
            below the route never builds one).  Its calendar is what *spec*
            resolves against; its scenario and ``as_of`` scope the loan fold
            behind the derived stop.

    Returns:
        The :class:`~app.services.recurrence.ResolvedRecurrence` with both
        halves of its closing, or ``None`` when the owner's schedule holds no
        pay periods -- the one refusal
        :func:`~app.services.recurrence.resolved_spec` answers rather than
        raises, passed through so the preview can say so in its own words.

    Raises:
        RecurrenceResolutionError: *spec* cannot be resolved against the
            owner's schedule -- a non-positive interval, a day the date leaves
            no room for, a first occurrence outside the authored window, or a
            spec stating another owner's ``user_id``.
        BaselineMissingError: The destination is a configured loan and *ctx*
            has no baseline scenario (ruling **R-R30**); see
            :func:`resolved_definition`.
    """
    resolved = resolved_spec(spec, ctx.calendar())
    if resolved is None:
        return None
    return _narrowed(
        definition, resolved, authored=resolved.closing.authored, ctx=ctx,
    )


def _narrowed(
    definition: RecurrenceOwner | UnsavedDefinition,
    resolved: ResolvedRecurrence, *,
    authored: EndBound, ctx: BalanceContext,
) -> ResolvedRecurrence:
    """Return *resolved* with its closing composed from *authored* and the destination.

    **The narrowing is applied HERE and only here** -- the sentence
    :func:`resolved_definition` carried alone until plan step R7d-f-2 gave
    the door a second entry.  The derived half is
    :func:`~app.services.loan_recurrence_sync.loan_payment_window`'s answer
    about *definition*'s destination; the authored half is whatever the
    caller established it to be (see :func:`resolved_submission` for the one
    way the two entries differ).  ``replace`` rather than a mutation because
    :class:`~app.services.recurrence.ResolvedRecurrence` is frozen.

    Args:
        definition: What is being read -- a stored template or an
            :class:`UnsavedDefinition`; only its ``to_account_id`` is read,
            through :func:`~app.services.recurring_transfer_query
            .destination_account`.
        resolved: The rule's or spec's resolution, whose authored closing the
            caller has already read and may have replaced.
        authored: The closing bound the OWNER stated, as the caller reads it.
        ctx: The read pass.

    Returns:
        *resolved* with ``closing`` holding both halves.
    """
    return replace(
        resolved,
        closing=Closing(
            authored=authored,
            derived=loan_payment_window(definition, resolved, ctx),
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

    **And they are walked ONCE per pass, since plan step R7d-f-2** (plan
    ledger row **N-513**): the walk is the pass's memo
    (:meth:`~app.services.balance_at.BalanceContext.placements_of`, keyed by
    the composed value), as the resolution has been since R16-b-2.  A
    ``/savings`` render reads a checking-to-goal transfer through this door
    in two sets and used to pay the walk twice; now the second read is memo
    hits end to end -- the resolution, the destination's loan state, the
    identity behind ruling R-R56, and the walk.  The reading also carries the
    horizon the walk reached (plan ledger row **N-514**), read off the same
    calendar the memo walked against.

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
    return RuleReading(
        resolved=resolved,
        placements=() if resolved is None else ctx.placements_of(resolved),
        horizon=ctx.calendar().horizon(),
    )


__all__ = [
    "UnsavedDefinition",
    "read_definition",
    "resolved_definition",
    "resolved_submission",
]
