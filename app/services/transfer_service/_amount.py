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

    **A LEG owns a figure exactly when the operator owns the pair's amount**,
    which is *the caller stated a figure* AND *the caller said a human authored
    it*.  Four cases, and each is a live call site:

    * **an amount with ``is_override`` that CHANGES the figure** -- the
      transfers page, where a human retyped the amount box.  Both legs stop
      deriving and own it, which is ruling **R-IO**: *the figure the owner types
      always wins*.  Without it the typed figure is discarded on the one shape
      where a leg's derivation is not its parent's amount -- a DERIVE-mode loan
      payment resolves to the loan's installment, so retyping ``$1,325.00`` over
      a ``$1,499.10`` PITI and marking it Paid would book the contract and lose
      the ``$174.10`` really paid.  The typed figure reaches the loan's split AS
      the cash, so interest / escrow / principal allocate against it.

      **The CHANGE test is load-bearing and a draft that omitted it was a money
      defect**, found by an adversarial review of this step.  The flag does NOT
      mean "a human retyped the amount": ``routes/transfers/mutations.py`` sets
      it when the amount changed **or the PERIOD did**, and that form renders
      the Amount input on every editable row while an HTML form posts every
      input it renders (measured, and recorded in that file at plan step
      X-f2-c3).  So a period-only move arrives here carrying an UNCHANGED
      amount beside ``is_override=True``, and taking ownership on the flag alone
      would have converted a derive-mode loan payment's legs to OWN, frozen for
      ever at ``transfers.amount`` -- the stale creation-time snapshot, ``$1.00``
      on the fixture.  That is finding **N-238**'s exposure being ADDED by the
      step whose docstring claimed to remove it.  Asking whether the figure
      moved is the same test the route already makes, applied where it decides
      something.
    * **an amount with no flag** -- the recurrence maintain pass, restating what
      the DEFINITION says.  Both legs are re-declared, which is what makes drift
      unconstructible over time: a write that is not the owner's ALWAYS restores
      the derivation, so a leg an owner took once is never left at a stale
      figure when its definition later moves.
    * **``is_override=False`` with no amount** -- the conflict resolver's *use
      the definition* action, which hands a row back without restating a price.
      The legs are re-declared, and **a first draft did not reach this case at
      all**: it ran only under ``"amount" in updates``, so clearing the flag
      left both legs OWNING the figure they had been frozen at, where the
      behaviour it replaced resumed deriving the moment the flag cleared.

    * **a bare ``is_override=True``** -- a period MOVE from carry-forward, which
      states no figure at all.  It changes no ownership: a leg an owner had
      taken keeps that figure through the move, and a derived leg keeps
      deriving.  The transfers page's period move reaches the arm ABOVE rather
      than this one, because its form echoes the amount; both land on "the
      figure did not move, so nobody authored it".
      **A first draft re-declared here** and would have discarded an owner's
      typed figure on a derive-mode loan payment the moment it was carried
      forward -- the one shape where a leg's derivation is not its parent's
      amount, so the revert was worth real money.

    **What a bare flag CANNOT do any more is FREEZE a derived leg**, and that is
    a deliberate difference from the behaviour this replaces rather than an
    oversight.  The flag froze a leg's live figure against every later
    derivation, which is finding **N-238**'s recorded exposure ("moving a row to
    another period silently freezes its amount"); ownership is DECLARED here, so
    a move that states no figure cannot claim one.  That is the fix falling out
    of the rule rather than being added to it -- inferring ownership from a flag
    that also records a period move is exactly what finding **N-262** removed
    from the resolver.  It moves ``$0.00`` on production, where a leg's derived
    figure is its parent's and no transfer is a loan payment.

    **Reading the caller's ``is_override`` at all is a WRITE-side translation,
    not N-262's inference** -- that finding is about PRICING reading the stored
    flag.  What this reads is the kwarg passed IN THIS CALL, and the three acts
    above are distinguishable there even though the stored column conflates them
    (finding **N-238**, plan step X-au-h).  **From scratch there would be no
    flag here at all**: once plan step X-au-f empties ``budget.transfers.amount``
    for a GENERATED transfer, "the owner authored this figure" IS "the parent
    owns its amount", and this parameter dissolves.

    Args:
        rows: The transfer and both shadows.
        stated_amount: The validated positive figure the caller stated, or
            ``None`` when this call states no amount.
        stated_override: ``True`` / ``False`` as the caller stated
            ``is_override`` in THIS call, or ``None`` when it stated nothing.
            The three values are three different acts and collapsing ``False``
            into ``None`` loses the conflict resolver's hand-back.  ``True``
            alone is not enough to take ownership -- see the CHANGE test above.
    """
    # Read BEFORE the parent is written: "did this call change the figure" is
    # the question, and one statement later the answer is always no.
    authored = stated_override and stated_amount != rows.transfer.amount
    if stated_amount is not None:
        state_own_amount(rows.transfer, stated_amount)
        for shadow in rows.shadows:
            if authored:
                state_own_amount(shadow, stated_amount)
            else:
                declare_derived(shadow, AmountSourceEnum.PARENT_TRANSFER)
    elif stated_override is False:
        for shadow in rows.shadows:
            declare_derived(shadow, AmountSourceEnum.PARENT_TRANSFER)
