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

from ._creations import NEW_ENVELOPE, NewEnvelope, PurchaseDestination
from ._policy import MerchantPolicy, PolicyAnswer, PolicyView


class PlacementKind(enum.Enum):
    """What a policy comes to for ONE creatable line.

    Three, because a policy that cannot be applied HERE is a different thing to
    say than one that names a row:

    * ``RECORD_IN`` -- an existing budget line in this line's own pay period;
    * ``CREATE_NEW`` -- an envelope this line's recording would create, and
      only where this period holds NONE of that name (finding **N-327**,
      developer ruling 2026-08-20).  It used to mint unconditionally, so a
      ``Lowe's -> a new "Home Improvement"`` answer applied to the developer's
      own statement made **4 envelopes across 3 pay periods** in one press, two
      of them in the SAME period, with the next statement adding more beside
      them.  No figure was wrong -- each envelope closes at its own purchases
      -- and what fragmented was the BUDGET.
      **It is still a SUGGESTION and never a substitution**: the degraded
      placement is PRINTED beside the line's own destination select, which
      still opens on *leave this line alone*, so the owner sees which envelope
      it would reuse and may pick another;
    * ``UNRESOLVED`` -- a policy exists and does not reach this line, with the
      reason it does not.  **Reported rather than substituted for**: the
      obvious substitution, falling back to a new envelope when the named
      template has no row in this period, would file money somewhere the owner
      never named.

    **There was a fourth, ``NOT_A_PURCHASE``, and ruling R-GJ deleted it** (plan
    step ``bank_import:X-ga``).  *Never a purchase* stopped being something a
    policy PLACES and became something that BARS: it is a
    :class:`~._bars.CreationBar` now, resolved before a destination is looked
    for, so the line never reaches this module at all.  Keeping a kind here for
    it would be a second statement of a refusal -- and what that second
    statement bought while it existed was a sentence saying "nothing here
    records it" printed directly beneath a select that did.
    """

    RECORD_IN = "record_in"
    CREATE_NEW = "create_new"
    UNRESOLVED = "unresolved"

    # The screen asks :class:`Placement`'s own questions rather than comparing
    # these strings: a Jinja condition restating a partition is a second place
    # for it to be wrong, and a typo in one of three literals falls through to
    # the arm that prints an unresolved reason -- ``None`` for the other two.
    # Named by adversarial financial review 2026-08-19.


@dataclass(frozen=True)
class Placement:
    """What the owner's policy comes to for one creatable line.

    Attributes:
        merchant: The line's merchant, which is the policy's key.
        kind: Which of the three (:class:`PlacementKind`).
        destination: The budget line to file into, for
            :attr:`PlacementKind.RECORD_IN`.  A
            :class:`~._creations.PurchaseDestination` drawn from the pass's own
            offer set rather than an id looked up here, so the screen shows the
            label and period span it would show anyway and the write door
            cannot be handed a row the screen may not offer.
        new_envelope: The envelope to create, for
            :attr:`PlacementKind.CREATE_NEW`.
        joins_new: Whether an EARLIER line in this same pass already creates
            that envelope, so this one would join it rather than make a second
            (finding **N-327**).  It stays a ``CREATE_NEW`` because the select
            value is unchanged -- one press mints one envelope per answer per
            period (:class:`~._create.MintedEnvelopes`) -- and what this flag
            buys is that the SCREEN says so before the press rather than after
            it.  Set by :func:`~._reads._creatable_lines`, which is the only
            reader that sees more than one line at a time.
        unresolved_reason: One sentence saying why the policy does not reach
            this line, for :attr:`PlacementKind.UNRESOLVED`.
    """

    merchant: str
    kind: PlacementKind
    destination: "PurchaseDestination | None" = None
    new_envelope: "NewEnvelope | None" = None
    joins_new: bool = False
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
    def sweep_class(self) -> "str | None":
        """Return which RISK class ticking this line would fall in.

        ``"into_open"`` files into a budget line that has not closed, which a
        reservation absorbs; ``"into_closed"`` raises what a CLOSED row records
        as its cost; ``"creates"`` mints a budget line the account did not
        have.  ``None`` for an UNRESOLVED placement, which names no row to act
        on.

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
        which option a policy means.  ``None`` for an UNRESOLVED placement --
        there is nothing to tick for a policy that does not reach here, and
        rendering a value for it would be a tick the owner never stated.
        """
        if self.kind is PlacementKind.RECORD_IN:
            return str(self.destination.transaction_id)
        if self.kind is PlacementKind.CREATE_NEW:
            return NEW_ENVELOPE
        return None


def _template_placement(
    policy: MerchantPolicy,
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
        policy: The stated answer, whose ``template_id`` is not ``None``.  It
            carries the merchant's NAME too (plan step ``bank_import:X-gd-1``),
            so the sentence and the placement name the same merchant by
            construction rather than because two arguments agreed.
        offered: The destinations open to THIS line -- already narrowed to its
            own pay period and to what no match has claimed.
        template_names: What to call each template, for the sentence.

    Returns:
        The :class:`Placement`.
    """
    merchant = policy.merchant
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


def _new_envelope_placement(
    policy: MerchantPolicy,
    offered: "list[PurchaseDestination]",
    view: PolicyView,
) -> Placement:
    """Resolve the NEW-ENVELOPE answer against one line's own period.

    **An answer naming an envelope by name is answered by an envelope of that
    name where one is already here** (finding **N-327**, developer ruling
    2026-08-20).  Creating unconditionally made a policy fragment its own
    budget line: measured on the developer's own statement, a ``Lowe's`` answer
    places 4 lines over 3 pay periods, so ONE press minted 4 envelopes -- and
    the next statement minted more beside them, because an ad-hoc row carries
    no identity across periods for anything to converge on.

    **Reusing is not the substitution this module refuses elsewhere.**  The
    substitution `_template_placement` declines is *falling back to a
    DIFFERENT KIND of destination when the named one is missing*, which files
    money somewhere the owner never named.  Here the owner named a name, and
    this is the row that has it.  It is still only a suggestion: the placement
    prints beside the line's own select, which opens on *leave this line
    alone*.

    **Two of them is a guess, so it is reported instead** -- the same rule, and
    the same sentence shape, `_template_placement` applies to a template that
    generated two rows in one period.  It is reachable on data this defect
    already produced, which is exactly why it may not be papered over now.

    Args:
        policy: The stated answer, whose ``answer`` is ``NEW_ENVELOPE``.  It
            carries the merchant's NAME too, for the reason
            :func:`_template_placement` states.
        offered: The destinations open to THIS line -- already narrowed to its
            own pay period and to what no match has claimed.
        view: What the owner has said and what it can resolve against.

    Returns:
        The :class:`Placement`.
    """
    merchant = policy.merchant
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
    # **The whole answer, never just its name.**  A policy's answer is a name
    # AND a category, and the within-press registry keys on both -- so matching
    # the name alone here made the two halves of one rule disagree, and would
    # have filed spending into a same-named envelope under a category the owner
    # did not pick.  The label is not compared either: it appends the
    # pay-period span for a reader.
    # **A TEMPLATE-generated row is excluded**, because naming a template is a
    # DIFFERENT answer with its own resolution beside this one
    # (:func:`_template_placement`, including its "this period holds two of
    # them" report).  An owner who means the recurring envelope has that answer
    # available and did not pick it; converging onto it here would make the two
    # answers indistinguishable in effect.
    named = [
        destination for destination in offered
        if destination.name == policy.envelope_name
        and destination.category_id == policy.category_id
        and destination.template_id is None
    ]
    if len(named) == 1:
        return Placement(
            merchant=merchant, kind=PlacementKind.RECORD_IN,
            destination=named[0],
        )
    if len(named) > 1:
        return Placement(
            merchant=merchant, kind=PlacementKind.UNRESOLVED,
            unresolved_reason=(
                f"You give {merchant} an envelope called "
                f"{policy.envelope_name}, and this pay period already holds "
                f"{len(named)} of them -- pick the one you mean."
            ),
        )
    return Placement(
        merchant=merchant, kind=PlacementKind.CREATE_NEW,
        new_envelope=NewEnvelope(
            name=policy.envelope_name, category_id=policy.category_id,
        ),
    )


def placements_for(
    merchant_id: "int | None",
    view: PolicyView,
    offered: "list[PurchaseDestination]",
) -> "Placement | None":
    """Return what the owner's policy comes to for ONE creatable line.

    Args:
        merchant_id: The line's merchant row, or ``None`` where the source
            names none -- which keys no policy at all, so the answer is
            ``None``.  **That is the whole reason the merchant is a nullable
            fact** (plan step X-f6a-3d): a reader that fell back to the
            description would key one policy for every truncated line a second
            adapter records and fire it on all of them.
        view: What the owner has said and what it can resolve against
            (:class:`PolicyView`).
        offered: The destinations open to this line, in its own pay period.

    Returns:
        The :class:`Placement`, or ``None`` when nothing is placed -- which is
        two facts this function deliberately does not distinguish, because
        neither puts anything beside the line's own control: the owner has not
        stated a policy for this merchant, or they have stated *never a
        purchase*.  **The second is a BAR, not a placement** (ruling **R-GJ**):
        it is answered by :meth:`~._bars.CreationBars.bar_for`, which
        :func:`~._reads._creatable_lines` asks first, so a line carrying that
        answer never reaches here at all.  The arm below stands anyway, because
        a total function may not fall through a stored answer into
        :func:`_template_placement` with a ``NULL`` template id.
    """
    if merchant_id is None:
        return None
    policy = view.policies.get(merchant_id)
    if policy is None:
        return None
    if policy.answer is PolicyAnswer.NEVER:
        return None
    if policy.answer is PolicyAnswer.NEW_ENVELOPE:
        return _new_envelope_placement(policy, offered, view)
    return _template_placement(policy, offered, view.template_names)
