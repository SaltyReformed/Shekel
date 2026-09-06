"""
Shekel Budget App -- Transfer Service: WHO OWNS a transfer's stated figure.

One act: a caller states an amount for a transfer, and this decides -- for the
parent and for each of its two legs -- whether that figure is the row's OWN or
the relation that prices it.  Ruling **R-FI**'s two states, applied to the three
rows a transfer is made of.

**It is a module of its own for the reason ``._endpoints`` and ``._status``
are** (plan step X-au-g-2c-2): ``._update`` ORCHESTRATES the doors, and every
concern that carries a rule of its own lives beside it rather than inside it.
The amount arm was four lines in that orchestration while a shadow held a COPY
of its parent's figure; it carries a decision now, and a decision with an
argument behind it belongs where the argument can be read.

Flask-isolated like the rest of the package: ORM rows in, nothing out.  Mutates
in place; does not flush and does not commit.
"""

from app.enums import AmountSourceEnum
from app.services.amount_ownership import declare_derived, state_own_amount
from app.services.transfer_service._validation import TransferRows


def apply_amount_ownership(
    rows: TransferRows, *, stated_amount, amount_authored, stated_override,
) -> None:
    """Write a stated amount, and settle WHO OWNS each of the three rows' figure.

    Called for every update that states an ``amount`` or an ``is_override``,
    because those are the two facts the answer depends on and either alone can
    change it.  Ruling **R-FI**'s two states, applied to the three rows a
    transfer is made of.

    **The parent takes any stated figure as its OWN.**
    ``budget.transfers.amount`` is what a caller states here, so the relation
    that priced the transfer is cleared with it --
    ``ck_transfers_amount_ownership`` pairs the two one-to-one, exactly as
    ``ck_transactions_amount_ownership`` does one table over.

    **A LEG's ownership has THREE answers, not two, and the third is what makes
    the rule total.**  A call either TAKES ownership for the operator, RESTORES
    the derivation, or SAYS NOTHING and leaves each leg exactly as it stands.
    Which one is decided by a single question asked FIRST -- *did a human author
    this figure?* -- which since ruling **R-JR** (plan step X-au-h) the CALLER
    states rather than this function reconstructing it:

    * a figure the caller says a human AUTHORED takes: someone retyped the
      amount box, so both legs stop deriving and own it.  Ruling **R-IO**.
      **This now covers an AD-HOC transfer's retype, which previously reached
      the restore arm instead**, because the old discriminator was
      ``is_override`` and the route raises that flag only for a template-linked
      row.  The figures agree either way -- an ad-hoc transfer can never be a
      derive-mode loan payment, since ``loan_payment_settings`` is keyed by
      ``transfer_template_id`` -- so this moves no money.  What it does is make
      an ad-hoc pair STORE the figure on all three rows where it used to store
      it once, which is the shape X-au-g-2c-2 removed for derived pairs and
      which X-au-m is the step that finishes.  Stated rather than left for a
      reader to discover, because the paragraph that documented the old arm was
      deleted with the comparison it described;
    * a figure NOBODY authored RESTORES: the only callers that state one are
      the maintain pass, which sends an amount exactly when the DEFINITION's
      differs from the row's, and the conflict resolver.  Either way the
      definition re-priced the transfer, so both legs are re-declared and read
      it.  This is what keeps drift unconstructible over time -- a leg an owner
      took once is never left at a stale figure when its definition later
      moves;
    * an explicit ``is_override=False`` RESTORES whatever it rides with: the
      conflict resolver's *use the definition*, which is the owner handing the
      pair back on purpose;
    * **everything else SAYS NOTHING** -- a bare flag carrying no figure at all.
      An ECHOED figure reaches this arm by never arriving: the door drops a
      figure no human authored rather than forwarding it, the idiom
      ``_update._grade_submitted_figure`` already applies to an echoed
      ``settled_amount`` one box over.

    **The question used to be *did this call MOVE the figure?*, answered by
    comparing the submitted amount against ``rows.transfer.amount``, and that
    comparison had to go rather than be corrected.**  It reads the very column
    plan step X-au-f empties for a generated transfer, at which point
    ``stated_amount != None`` is true for every call: the predicate stops
    discriminating and answers YES unconditionally, so every save takes
    ownership on the flag alone.  That is finding **N-436**, and finding
    **N-448** is the same defect one layer up in the route.  Both are CLOSED by
    this parameter rather than guarded by it -- in THIS function there is no
    comparison left to go vacuous, which is why the fix is a deletion and not a
    NULL check.

    **The CLASS is not closed, and an earlier revision of this paragraph said
    it was.**  ``._settle`` still asks ``booked != rows.transfer.amount`` to
    decide whether to log ``EVT_TRANSFER_AMOUNT_FROZEN``, which is the same
    column and the same vacuity: once X-au-f empties it, that comparison is
    true for every settle and the event fires on all of them, including plain
    savings transfers.  It is observability rather than money, and it belongs
    to X-au-f because X-au-f is what arms it -- but "there is no comparison
    left" was a claim about the whole predicate class made after re-grepping
    only the sites this step touched, which is exactly the mistake
    `feedback_a_closed_row_needs_its_predicate_regrepped` names.

    **Omitting the third answer is not a smaller rule, it is a WRONG one**, and
    this arc has now paid for it twice in the same function.  With only *take*
    and *restore*, every write that asserts nothing about authorship falls into
    *restore* and revokes an ownership it was never asked to judge.  Both
    instances cost the same `$174.10` on the same shape -- a DERIVE-mode loan
    payment, the one case where a leg's derivation is NOT its parent's amount,
    so handing it back reverts the owner's ``$1,325.00`` to the contract's
    ``$1,499.10``:

    * **the PERIOD move.**  ``routes/transfers/mutations.py`` raises the flag
      when the amount changed **or the period did**, and the form posts every
      input it renders, so a period-only save arrives with the amount echoed
      beside a raised flag.  A first fix stopped that echo TAKING a derived leg
      and left it GIVING BACK a taken one.
    * **the UNRELATED-FIELD save**, which is the door that gets used.  The route
      raises the flag only on an amount or period DELTA, so a save that changes
      NOTES, CATEGORY or STATUS carries the echoed amount and **no flag at
      all** -- and the fix for the period move, written inside the flag branch,
      did not reach it.  Worse than its sibling, because ownership is decided at
      ``_update._apply_transfer_updates`` BEFORE ``_dispatch_settle`` runs: a
      status-only save to Done restored the legs and then booked the contract.

    **Both instances are now UNREACHABLE rather than guarded**, and the two
    tests that grade them are kept for exactly that reason.  Each was an ECHOED
    figure reaching this function at all; since plan step X-au-h the door drops
    a figure no human authored, so neither shape arrives.  The tests pass for a
    different reason than they used to, which is the interesting property: they
    were written against a comparison that no longer exists and they still
    describe the behaviour the doors must have.

    Both are graded, by
    ``test_the_FORMs_period_move_does_not_HAND_BACK_a_taken_leg`` and
    ``test_an_UNRELATED_field_save_does_not_HAND_BACK_a_taken_leg``.  The lesson
    is in the SHAPE rather than in either instance: asking *did the figure move*
    first, once, for every caller, is what makes the third answer unreachable by
    accident.  Asking it inside one branch answers it for one door.

    **What a bare flag CANNOT do is FREEZE a derived leg**, and that is a
    deliberate difference from the behaviour this replaces.  The flag froze a
    leg's live figure against every later derivation, which is finding
    **N-238**'s recorded exposure ("moving a row to another period silently
    freezes its amount"); ownership is DECLARED here, so a move that states no
    figure cannot claim one.  Inferring ownership from a flag that also records
    a period move is exactly what finding **N-262** removed from the resolver.

    **Reading the caller's ``is_override`` at all is a WRITE-side translation,
    not N-262's inference** -- that finding is about PRICING reading the stored
    flag.  What this reads is the kwarg passed IN THIS CALL, and the acts above
    are distinguishable there even though the stored column conflates them
    (finding **N-238**, plan step X-au-h).  **From scratch the flag would not be
    read here at all**: the fact this function actually wants is *did a human
    author this figure*, and it is reconstructing that from a flag that means
    two things plus a value comparison.  X-au-h is what makes it a fact.

    **``is_override`` no longer decides this, and that is the point of plan step
    X-au-h.**  The flag carried "a human authored this figure" as one of several
    meanings, so reading it here was reconstructing an authorship fact from a
    column that also records a period move.  It means exactly one thing now --
    *this row is the OWNER's, not the rule's* -- and authorship is stated by the
    caller.  ``stated_override`` survives for the ONE act nothing else
    expresses: the conflict resolver's explicit hand-back.  It may ride WITH a
    figure or without one -- ``transfer_recurrence.resolve_conflicts`` sends
    ``is_override=False`` and adds the definition's new amount when it has one
    -- and both reach the definition arm, by different branches.  An earlier
    revision said the hand-back "carries no figure", which is true of only one
    of its two shapes.

    Args:
        rows: The transfer and both shadows.
        stated_amount: The validated positive figure the caller stated, or
            ``None`` when this call states no amount.
        amount_authored: Whether a HUMAN authored *stated_amount* (ruling
            **R-JR**).  Stated by the caller because only the caller can know
            it: a door knows whether the figure differs from the one it
            rendered, and a service caller knows whether it is the definition
            speaking.  Meaningless when *stated_amount* is ``None``, and read
            only on that arm.
        stated_override: ``False`` when the caller explicitly hands the pair
            back to its definition, otherwise ``True`` or ``None``.  Only the
            ``False`` is acted on -- it is the conflict resolver's *use the
            definition* -- and collapsing it into ``None`` would lose that act.
            It no longer decides who owns a leg's figure; *amount_authored*
            does.
    """
    if stated_amount is not None:
        state_own_amount(rows.transfer, stated_amount)
        # A figure arrived, so this call DOES say who priced the pair: the
        # operator when it says a human authored it, the definition otherwise.
        for shadow in rows.shadows:
            if amount_authored:
                state_own_amount(shadow, stated_amount)
            else:
                declare_derived(shadow, AmountSourceEnum.PARENT_TRANSFER)
        return

    if stated_override is False:
        # The HAND-BACK, which needs no figure: the owner is saying the
        # definition prices this pair again.
        for shadow in rows.shadows:
            declare_derived(shadow, AmountSourceEnum.PARENT_TRANSFER)
        return

    # Nothing in this call speaks to authorship -- a bare flag carrying no
    # figure. Leave each leg as it stands, in EITHER direction.
