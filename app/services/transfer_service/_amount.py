"""
Shekel Budget App -- Transfer Service: WHO OWNS a transfer's stated figure.

One act: a caller states a transfer's amount OWNERSHIP, and this applies it to
the parent and to each of its two legs.  Ruling **R-FI**'s two states, applied
to the three rows a transfer is made of.

**The caller states ONE value since plan step X-au-f, and ruling R-BAL11 is
why.**  It took three parameters -- a figure, whether a human authored it, and
the conflict resolver's explicit hand-back -- which are three spellings of one
question, *what prices this row*.  A trace measured X-au-f's own claim that the
third dissolves when ``budget.transfers.amount`` empties as FALSE: on its own
that parameter becomes the ONLY spelling of the hand-back.  What dissolves all
three is the caller stating an
:class:`~app.models.amount_ownership.AmountOwnership`, where *the owner's, and
here is the figure* and *its definition's* are one value and ABSENT means this
save says nothing about the amount.

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
from app.services.amount_ownership import derived_ownership
from app.services.transfer_service._validation import TransferRows


def apply_amount_ownership(rows: TransferRows, ownership) -> None:
    """Apply one stated AMOUNT OWNERSHIP to the transfer and both its legs.

    Called for every update that states one.  Ruling **R-FI**'s two states,
    applied to the three rows a transfer is made of, and ruling **R-BAL11**'s
    one parameter.

    **The PARENT takes the stated ownership verbatim.**  It is the row the
    caller is talking about, and since plan step X-au-f it is also the row that
    decides owner-or-contract for the whole pair (ruling **R-IO**): a figure
    here is the owner's, an absent one hands the pair to the definition that
    generated it.

    **A LEG's ownership FOLLOWS the parent's, and there are two answers where
    there used to be three.**  A leg is its parent's projection, so:

    * the parent OWNS a figure -- someone retyped the amount box -- and each leg
      stores that same figure.  **This is the one clause that is still a COPY**,
      and plan step ``X-au-m`` is where it goes: under ruling **R-JM** a leg
      reads its parent unconditionally, at which point a leg holds no figure in
      either state and Transfer Invariant 3's amount clause is structural rather
      than maintained;
    * the parent DERIVES -- its definition prices it -- and each leg declares
      :attr:`~app.enums.AmountSourceEnum.PARENT_TRANSFER` and stores nothing,
      which is what plan step X-au-g-2c-2 already made a leg's birth state.

    **The THIRD answer is gone because it has no caller left to express it.**
    It was *say nothing and leave each leg as it stands*, and it existed because
    a bare ``is_override`` flag could reach this function carrying no figure at
    all.  A caller states an ownership or it does not; ABSENCE is now expressed
    by not calling this at all (``._update`` asks whether the key is present),
    so there is no arm here that can revoke an ownership it was not asked to
    judge.  **That arm's absence is what cost `$174.10` twice** -- on a
    DERIVE-mode loan payment, where handing a leg back reverted the owner's
    ``$1,325.00`` to the contract's ``$1,499.10`` -- once through the FORM's
    period move and once through an UNRELATED-FIELD save, and both are graded by
    ``test_the_FORMs_period_move_does_not_HAND_BACK_a_taken_leg`` and
    ``test_an_UNRELATED_field_save_does_not_HAND_BACK_a_taken_leg``.  Those
    tests now describe a door that cannot reach this function without meaning
    to.

    **Reading ``is_override`` here is gone with the parameter that carried it.**
    The flag means exactly one thing since plan step X-au-h -- *this row is the
    OWNER's, not the rule's* -- and what this function wanted was never the flag
    but *what prices this row*, which the caller now says outright.  The
    conflict resolver's explicit hand-back is spelled as
    ``derived_ownership(TEMPLATE)`` like every other statement that the
    definition prices the pair.

    Args:
        rows: The transfer and both shadows.
        ownership: The :class:`~app.models.amount_ownership.AmountOwnership`
            the caller states for the PARENT -- ``own`` over a figure a human
            authored, or ``derived`` naming
            :attr:`~app.enums.AmountSourceEnum.TEMPLATE` when its definition
            prices it.  Never ``None``: a caller with nothing to say about the
            amount does not call this.
    """
    rows.transfer.amount_ownership = ownership
    leg_ownership = (
        ownership if ownership.figure is not None
        else derived_ownership(AmountSourceEnum.PARENT_TRANSFER)
    )
    for shadow in rows.shadows:
        shadow.amount_ownership = leg_ownership
