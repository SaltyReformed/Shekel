"""What a stated policy comes to for ONE unexplained bank line.

Plan step ``bank_import:X-f6a-3d``.  :mod:`._policy` holds the ANSWER -- where
the owner has said a merchant's spending goes -- and this holds what that
answer means for one line of one statement, which is a different question with
a different shape: an answer is period-independent by construction, and a
placement is the one budget line in the one pay period that line falls in.

**Every way an answer can fail to reach a line is REPORTED, never substituted
for.**  A template that generated no offerable row in this period, two rows
where one was expected, a category since archived: each is a
:attr:`PlacementKind.UNRESOLVED` carrying the sentence that says which.
Substituting -- falling back to a new envelope when the named one is missing --
is how a suggestion becomes a guess, and it would file money somewhere the
owner never named.

**Nothing here writes anything**, which is the property the whole step rests
on: a placement is rendered BESIDE a line's destination select, never into it,
and the select still opens on *leave this line alone* (ruling **R-FZ**).

Services-boundary discipline: plain data in, frozen dataclasses out, no Flask
import, no clock read, no query -- every fact it needs arrives on a
:class:`~._policy.PolicyView`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from ._offers import NEW_ENVELOPE, NewEnvelope, PurchaseDestination
from ._policy import MerchantPolicy, PolicyAnswer, PolicyView


class PlacementKind(enum.Enum):
    """What a policy comes to for ONE creatable line.

    Four, because a policy that cannot be applied HERE is a different thing to
    say than one that says do nothing:

    * ``RECORD_IN`` -- an existing budget line in this line's own pay period;
    * ``CREATE_NEW`` -- an envelope this line's recording would create.  **One
      PER LINE, not per period** (finding **N-327**): the create door mints
      unconditionally, so two lines of one merchant inside one pay period make
      two envelopes, and nothing here degrades the second to a RECORD_IN
      against the first.  Reported on the screen rather than substituted for,
      which is this module's rule everywhere else -- silently filing into a
      same-named row would be the app choosing a destination the owner did not
      name;
    * ``NOT_A_PURCHASE`` -- the owner said never;
    * ``UNRESOLVED`` -- a policy exists and does not reach this line, with the
      reason it does not.  **Reported rather than substituted for**: the
      obvious substitution, falling back to a new envelope when the named
      template has no row in this period, would file money somewhere the owner
      never named.
    """

    RECORD_IN = "record_in"
    CREATE_NEW = "create_new"
    NOT_A_PURCHASE = "not_a_purchase"
    UNRESOLVED = "unresolved"

    # The screen asks :class:`Placement`'s own questions rather than comparing
    # these strings: a Jinja condition restating a partition is a second place
    # for it to be wrong, and a typo in one of four literals falls through to
    # the arm that prints an unresolved reason -- ``None`` for the other three.
    # Named by adversarial financial review 2026-08-19.


@dataclass(frozen=True)
class Placement:
    """What the owner's policy comes to for one creatable line.

    Attributes:
        merchant: The line's merchant, which is the policy's key.
        kind: Which of the four (:class:`PlacementKind`).
        destination: The budget line to file into, for
            :attr:`PlacementKind.RECORD_IN`.  A
            :class:`~._offers.PurchaseDestination` drawn from the pass's own
            offer set rather than an id looked up here, so the screen shows the
            label and period span it would show anyway and the write door
            cannot be handed a row the screen may not offer.
        new_envelope: The envelope to create, for
            :attr:`PlacementKind.CREATE_NEW`.
        unresolved_reason: One sentence saying why the policy does not reach
            this line, for :attr:`PlacementKind.UNRESOLVED`.
    """

    merchant: str
    kind: PlacementKind
    destination: "PurchaseDestination | None" = None
    new_envelope: "NewEnvelope | None" = None
    unresolved_reason: "str | None" = None

    @property
    def records_in(self) -> bool:
        """Return whether this places the line in a budget line that exists."""
        return self.kind is PlacementKind.RECORD_IN

    @property
    def creates(self) -> bool:
        """Return whether this places the line in an envelope it would make."""
        return self.kind is PlacementKind.CREATE_NEW

    @property
    def is_not_a_purchase(self) -> bool:
        """Return whether the owner said this merchant is never a purchase."""
        return self.kind is PlacementKind.NOT_A_PURCHASE

    @property
    def sweep_class(self) -> "str | None":
        """Return which RISK class ticking this line would fall in.

        ``"into_open"`` files into a budget line that has not closed, which a
        reservation absorbs; ``"into_closed"`` raises what a CLOSED row records
        as its cost; ``"creates"`` mints a budget line the account did not
        have.  ``None`` for a placement that is not an act at all.

        **A PARTITION, and it exists because ruling R-FZ(c) already demanded
        one.** That ruling swept proposals per CLASS rather than by one "tick
        all", on the ground that *the riskiest class may not ride the same
        click as the safest*, and :attr:`~._offers.MatchProposal.review_class`
        is what makes it server-derived.  A first draft of this sweep ticked
        every placement together -- and measured on a production clone, **29 of
        account 1's 220 offerable destinations have already closed**, including
        8 of 60 Gas rows and 9 of 61 Groceries rows, so a template answer
        naming either would have raised a closed row's recorded cost on the
        same press that filled an open one.  Found by two adversarial reviews
        2026-08-19.

        Derived HERE rather than as a Jinja condition for the reason
        ``review_class`` is: a template restating the partition is a second
        place for it to be wrong.
        """
        if self.kind is PlacementKind.CREATE_NEW:
            return "creates"
        if self.kind is not PlacementKind.RECORD_IN:
            return None
        return "into_closed" if self.destination.is_settled else "into_open"

    @property
    def select_value(self) -> "str | None":
        """Return what this line's destination select would be set to.

        **The ONE place the sweep's target value is decided**, so the control
        that ticks a line and the door that writes it cannot disagree about
        which option a policy means.  ``None`` for a placement that is not an
        act -- there is nothing to tick for *never a purchase* or for a policy
        that does not reach here, and rendering a value for either would be a
        tick the owner never stated.
        """
        if self.kind is PlacementKind.RECORD_IN:
            return str(self.destination.transaction_id)
        if self.kind is PlacementKind.CREATE_NEW:
            return NEW_ENVELOPE
        return None


def _template_placement(
    policy: MerchantPolicy,
    merchant: str,
    offered: "list[PurchaseDestination]",
    template_names: "dict[int, str]",
) -> Placement:
    """Resolve the TEMPLATE answer against one line's own period.

    **A template does not always produce exactly one row in a period, and
    assuming it did would file money in a row the owner did not pick.**
    Measured on a 2026-08-18 production clone: template 22
    (``Kayla's Spending Money``) generated TWO rows in pay period 3, ids 2388
    and 2389.  So the three cases are all real and all reported:

    * exactly one offerable row -- the placement;
    * none -- the template made no row here, or the one it made cannot take a
      purchase (closed at a stored figure, already matched, cancelled).
      Measured: template 5 (``Gas``) is offerable in 9 of the 11 periods the
      developer's creatable lines fall in, and template 38 (``Groceries``) in
      10;
    * more than one -- which of them the owner meant is a guess, and this
      module does not make guesses.

    Args:
        policy: The stated answer, whose ``template_id`` is not ``None``.
        merchant: The line's merchant.
        offered: The destinations open to THIS line -- already narrowed to its
            own pay period and to what no match has claimed.
        template_names: What to call each template, for the sentence.

    Returns:
        The :class:`Placement`.
    """
    matches = [
        destination for destination in offered
        if destination.template_id == policy.template_id
    ]
    named = template_names.get(policy.template_id)
    if len(matches) == 1:
        return Placement(
            merchant=merchant, kind=PlacementKind.RECORD_IN,
            destination=matches[0],
        )
    if not matches:
        return Placement(
            merchant=merchant, kind=PlacementKind.UNRESOLVED,
            unresolved_reason=(
                f"You file {merchant} in {named or 'a recurring envelope'}, "
                f"and this pay period has none that can take a purchase -- it "
                f"may not have been generated here, or it may have closed at a "
                f"fixed figure."
            ),
        )
    return Placement(
        merchant=merchant, kind=PlacementKind.UNRESOLVED,
        unresolved_reason=(
            f"You file {merchant} in {named or 'a recurring envelope'}, and "
            f"this pay period holds {len(matches)} of them -- pick the one you "
            f"mean."
        ),
    )


def placements_for(
    merchant: "str | None",
    view: PolicyView,
    offered: "list[PurchaseDestination]",
) -> "Placement | None":
    """Return what the owner's policy comes to for ONE creatable line.

    Args:
        merchant: The line's merchant, or ``None`` where the source names
            none -- which keys no policy at all, so the answer is ``None``.
            **That is the whole reason the merchant is a nullable COLUMN**
            (plan step X-f6a-3d): a reader that fell back to the description
            would key one policy for every truncated line a second adapter
            records and fire it on all of them.
        view: What the owner has said and what it can resolve against
            (:class:`PolicyView`).
        offered: The destinations open to this line, in its own pay period.

    Returns:
        The :class:`Placement`, or ``None`` when the owner has not stated one
        for this merchant.  ``None`` and
        :attr:`PlacementKind.NOT_A_PURCHASE` are different answers and the
        screen says them differently.
    """
    if merchant is None:
        return None
    policy = view.policies.get(merchant)
    if policy is None:
        return None
    if policy.answer is PolicyAnswer.NEVER:
        return Placement(merchant=merchant, kind=PlacementKind.NOT_A_PURCHASE)
    if policy.answer is PolicyAnswer.NEW_ENVELOPE:
        if policy.category_id not in view.active_categories:
            return Placement(
                merchant=merchant, kind=PlacementKind.UNRESOLVED,
                unresolved_reason=(
                    f"You give {merchant} a new envelope called "
                    f"{policy.envelope_name}, and the category you filed it "
                    f"under has been archived -- so nothing can be created for "
                    f"it until you answer for {merchant} again."
                ),
            )
        return Placement(
            merchant=merchant, kind=PlacementKind.CREATE_NEW,
            new_envelope=NewEnvelope(
                name=policy.envelope_name, category_id=policy.category_id,
            ),
        )
    return _template_placement(policy, merchant, offered, view.template_names)
