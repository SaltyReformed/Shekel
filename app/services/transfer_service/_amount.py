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
    rows: TransferRows, *, stated_amount, stated_override,
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
    Which one is decided by a single question asked FIRST -- *did this call move
    the figure?* -- because a figure is the only thing that carries new
    information about who priced the pair:

    * a figure that MOVED **with** ``is_override`` TAKES: a human retyped the
      amount box, so both legs stop deriving and own it.  Ruling **R-IO**.
      The route raises that flag only for a TEMPLATE-LINKED transfer
      (``mutations.py`` gates on ``transfer_template_id``), so an ad-hoc
      transfer's retype reaches the arm below instead -- which costs nothing,
      because an ad-hoc parent owns its amount and the leg derives to the same
      figure.  A loan payment is always template-linked, so the shape that
      MATTERS is always on this arm;
    * a figure that MOVED **without** it RESTORES: the DEFINITION re-priced the
      transfer, so both legs are re-declared and read it;
    * an explicit ``is_override=False`` RESTORES whatever it rides with: the
      conflict resolver's *use the definition*, which is the owner handing the
      pair back on purpose;
    * **everything else SAYS NOTHING** -- an ECHOED figure, with the flag or
      without it, and a bare flag carrying no figure at all.

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

    **A WARNING for plan step X-au-f, and it is the comparison rather than the
    parameter.**  ``figure_moved`` reads ``rows.transfer.amount``.  X-au-f
    declares a generated PARENT derived, which sets that column to ``NULL`` --
    at which point ``stated_amount != None`` is true for every call, so
    ``figure_moved`` stops discriminating and becomes VACUOUSLY TRUE.  Every
    ``is_override=True`` save would then take ownership on the flag alone, which
    is the exposure the MOVED test exists to prevent, re-added silently.  It
    fails by always answering yes rather than by raising, so nothing here would
    catch it.  Recorded as finding **N-436**, owned by X-au-f.

    Args:
        rows: The transfer and both shadows.
        stated_amount: The validated positive figure the caller stated, or
            ``None`` when this call states no amount.
        stated_override: ``True`` / ``False`` as the caller stated
            ``is_override`` in THIS call, or ``None`` when it stated nothing.
            The three values are three different acts and collapsing ``False``
            into ``None`` loses the conflict resolver's hand-back.  ``True``
            alone is not enough to take ownership -- the figure must have MOVED.
    """
    # Asked BEFORE the parent is written, and asked FIRST: one statement later
    # the answer is always no, and one branch later it is only asked for one
    # caller.  A figure that did not move carries no information about who
    # priced this pair, whatever flag rides beside it.
    figure_moved = (
        stated_amount is not None and stated_amount != rows.transfer.amount
    )
    if stated_amount is not None:
        state_own_amount(rows.transfer, stated_amount)

    if figure_moved:
        # The figure MOVED, so this call DOES say who priced the pair: the
        # operator if it says a human authored it, the definition otherwise.
        # The definition arm is what keeps drift unconstructible over time --
        # a leg an owner took once is never left at a stale figure when its
        # definition later moves.
        for shadow in rows.shadows:
            if stated_override:
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

    # Nothing in this call speaks to authorship -- an echoed figure, or a bare
    # flag. Leave each leg as it stands, in EITHER direction.
