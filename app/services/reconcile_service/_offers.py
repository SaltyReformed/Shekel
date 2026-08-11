"""
Shekel Budget App -- What the reconcile panel renders

The value types the reconcile step publishes across the services boundary:
one tickable purchase, the block it nests in, and the whole offer set.  They
live together because they are ONE shape -- the panel's list -- and because
every arm of the package contributes to them: the purchase arm fills
:attr:`OutstandingGroup.purchases` today, and plan steps X-f2-c2 and X-f2-c3
add the parent's own close tick, bills and transfer shadows to the same
blocks.  A type per arm would put the panel's shape in three places and make
the next arm's field a fourth.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Frozen dataclasses, no behaviour beyond the two
    derivations :class:`OutstandingSet` owns.
  - All monetary values are :class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class OutstandingPurchase:
    """One purchase the account has not been seen to have paid for.

    A VALUE, not the ORM row: the panel renders four fields and the form posts
    one id, and publishing the row itself is what let the template reach
    ``entry.transaction.name`` per line -- one lazy SELECT per purchase, on a
    relationship the grouping has already loaded (see
    :func:`app.services.reconcile_service.outstanding_set`).

    Attributes:
        entry_id: The ``budget.transaction_entries`` id, and the value the
            tick posts back.  Re-scoped by
            :func:`~app.services.reconcile_service.record_settled_days` rather
            than trusted, so publishing it grants nothing.
        purchased_on: The day the purchase was MADE -- the budget clock, and
            what the row is captioned with.  Never the day it settled: an
            outstanding purchase has no settle day, which is the definition.
        description: The purchase's own description, as typed.
        amount: The purchase's amount, positive.
    """

    entry_id: int
    purchased_on: date
    description: str
    amount: Decimal


@dataclass(frozen=True)
class OutstandingGroup:
    """One source row, with the purchases against it that are still outstanding.

    **The grouping is ruling R-EW's shape**: a purchase nests under the thing it
    belongs to, so a grocery purchase and the grocery envelope read as one
    block.  Rejected there: grouping by act-type, which separates exactly those
    two, and a flat undifferentiated list, which is what shipped at S1-c and
    named each purchase's parent in a trailing fragment per line instead.

    **It is a GROUP and not an ``OutstandingEnvelope``, on two grounds.**
    Nothing in the purchase arm's scope asserts the parent is envelope-tracked
    -- the clauses ask for a projected, non-deleted parent on this account, and
    "it has purchases" is what makes it one in practice -- so the narrower name
    would claim a classification no clause checks.  And plan step X-f2-c2 puts
    BILLS and transfer shadows in this same set, neither of which is an
    envelope, so the narrow name would have to be renamed inside a leaf that
    moves money.

    Attributes:
        transaction_id: The parent ``budget.transactions`` id, and the key the
            grouping is built on.  Published because plan step X-f2-c2 adds the
            parent's OWN close tick to this block and posts it; nothing in this
            leaf posts it.
        name: The parent's name, for the block's heading.
        period_start: The first day of the pay period the parent is budgeted
            in, so the heading names WHICH one.  **Without it two blocks can
            carry the identical heading**: the recurrence engine materialises
            one row per template per period, so one envelope in two periods is
            two parents with one name, and both can hold outstanding purchases
            at one assertion.  The flat list was equally ambiguous per line;
            grouping PROMOTES that ambiguity to the heading, so the leaf that
            creates the heading is the leaf that has to resolve it.
        period_end: Its last day, so the caption is a span rather than a date
            the user has to look up.
        purchases: Its outstanding purchases, oldest first.  Never empty -- a
            parent with nothing outstanding is not a block, and
            :func:`app.services.reconcile_service.outstanding_set` does not
            build one.
        total: The sum of :attr:`purchases`, quantised by the source amounts
            rather than by any arithmetic here.
    """

    transaction_id: int
    name: str
    period_start: date
    period_end: date
    purchases: "tuple[OutstandingPurchase, ...]"
    total: Decimal


@dataclass(frozen=True)
class OutstandingSet:
    """The PURCHASES a statement of one civil day could still settle, grouped.

    **Its scope is purchases, and every field says so** (ruling R-EW widens the
    OFFER to bills, the parent's own close and transfer shadows at plan steps
    X-f2-c2 and X-f2-c3; this leaf ships the first of the four).  A field called
    ``total`` here would be read by the next leaf as "the sum of every tickable
    row", and that figure DOUBLE-COUNTS the moment the close tick joins: an
    envelope settles at ``sum(entries)`` (ruling **R-FA**), so counting its two
    `$40` / `$60` purchases AND its `$100` close reports `$200` against `$100`
    of money.  Naming the fields for what they hold is what stops the next leaf
    implementing a wrong definition literally.

    Attributes:
        groups: One block per parent carrying outstanding purchases, ordered by
            that parent's oldest outstanding purchase so the block a user is
            most likely looking for is first.
        purchase_count: How many purchases the set offers.  Computed here and
            not in the template because it is the figure the panel's copy
            pluralises on, and money-adjacent counting belongs on the services
            side of the boundary.
        purchase_total: The sum of those purchases.
    """

    groups: "tuple[OutstandingGroup, ...]"
    purchase_count: int
    purchase_total: Decimal

    @classmethod
    def empty(cls) -> "OutstandingSet":
        """Return the set for an account with nothing to reconcile.

        The ROUTE's shape for an account carrying no assertion at all: there is
        no day for a purchase to be inside of, so the producer is never asked.
        It is a constructor here rather than a literal there because the zero
        is MONEY, and the services boundary is where money is built (a route
        composing ``Decimal("0.00")`` is the shape ``outstanding_total`` had
        before plan step X-f2-c1, and it is how a caller ends up quantising).
        """
        return cls(groups=(), purchase_count=0, purchase_total=Decimal("0.00"))

    @property
    def is_empty(self) -> bool:
        """Return True when the account has nothing outstanding.

        The steady state for a user who reconciles as they go, and the state
        the panel answers with its "nothing is being held back twice" copy
        rather than an empty form.

        **Read off the COUNT, not off ``groups``**, and the difference is a
        wrong empty state rather than a style point.  ``not self.groups`` is
        the same answer as this ONLY while purchases are the whole offer set:
        the moment X-f2-c2 adds a kind that does not arrive inside a group, it
        answers True for a panel with things to offer and suppresses them
        behind the "nothing is being held back twice" copy.  Whether bills DO
        arrive outside a group is the open shape question
        (:mod:`app.services.reconcile_service._assemble`); this accessor is
        written so that either answer is safe, because one definition of empty
        -- "nothing to tick" -- survives both.
        """
        return self.purchase_count == 0
