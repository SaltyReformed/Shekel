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

import enum
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class OfferKind(enum.Enum):
    """What a block IS, for the two rules ruling **R-FC** puts on the order.

    The panel is ONE collection of blocks (R-FC rejected two held side by side),
    and two of that ruling's three presentational rules are about this value:
    the ordering key gains a kind term so like sits with like, and a section
    label is emitted where the kind changes.  The third -- a childless block
    prints inline -- is a property of the block's contents, not of its kind.

    **The member's VALUE is the section heading the panel prints**, and
    :attr:`section_label` says so at the call site, because a bare ``.value``
    does not tell a reader the string is user-visible copy.

    **The definition ORDER is the section order**, so adding plan step
    X-f2-c3's ``TRANSFER`` member below ``BILL`` is the whole of that leaf's
    ordering change.  A rank map beside the class would be a second place to
    edit, and an omission there sorts a whole section to the wrong end rather
    than failing.

    It is a service-tier classification and not a ``ref`` table, so it carries
    no id and nothing compares it to a string: the assembler reads
    :attr:`rank`, the template reads a label the assembler already resolved.
    """

    ENVELOPE = "Envelopes"
    BILL = "Bills"

    @property
    def rank(self) -> int:
        """Return this kind's position in the panel's section order.

        Returns:
            The member's index in its own class's definition order.
        """
        return list(type(self)).index(self)

    @property
    def section_label(self) -> str:
        """Return the heading the panel prints above this kind's first block.

        Returns:
            The user-visible section heading.
        """
        return self.value


@dataclass(frozen=True)
class OutstandingTransaction:
    """The source ROW itself, offered -- the envelope's close, or a bill.

    The TRANSACTION arm's offer (plan step X-f2-c2, ruling **R-FA**), beside
    the purchase arm's :class:`OutstandingPurchase`.  Ticking one settles the
    row through ``transaction_service.settle_transaction`` -- the same verb the
    grid's Mark Paid calls -- stamping the statement's own day.

    Attributes:
        transaction_id: The ``budget.transactions`` id, and the value the tick
            posts back.  Re-scoped by the arm's writer rather than trusted, so
            publishing it grants nothing.
        attributed_on: The day the PROJECTION lands this row on -- its
            ``due_date``, falling back to its pay period's start and clamped
            into that period (:func:`~app.utils.dates.attribution_date`).  It
            is the day the panel captions the row with, and the same day the
            offer's own bound is measured against, so a row cannot be offered
            under a caption that disagrees with why it was offered.
        amount: **What ticking this row will BOOK**, resolved by
            ``transaction_service.settle_amount`` rather than read off a
            column.  The panel showing a figure the verb would not book is this
            arc's own root cause 1 applied to a screen, and it is why this
            field is not ``effective_amount``.
        is_correctable: Whether the panel renders :attr:`amount` as an editable
            input (ruling **R-FB**, sharpened by **R-FF**): true exactly when
            the settle verb takes its MANUAL branch, i.e. the row is not
            (envelope-tracked AND carrying entries).  Read off
            ``transaction_service.settles_from_entries`` so the panel cannot
            offer a box for a value the verb would ignore.
        is_income: Whether this row is money ARRIVING.  The panel counts
            deposits separately from payments (ruling **R-FD**) because a
            deposit and a bill do not sum to anything a reader wants.
    """

    transaction_id: int
    attributed_on: date
    amount: Decimal
    is_correctable: bool
    is_income: bool


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
        purchases: Its outstanding purchases, oldest first.  **May be EMPTY
            since plan step X-f2-c2**, and ruling **R-FC** is what that means
            for the screen: a childless block prints its name inline as one row
            rather than as a heading above a one-item list.  A bill is always
            childless; an envelope is childless when nothing it holds is
            outstanding but the row itself is.  A block with neither purchases
            nor a :attr:`settle` offer is never built.
        settle: The parent's OWN tick, or ``None`` when the row itself is not
            offerable -- the everyday mid-period case, where an envelope holds
            outstanding purchases but its own attribution day has not passed.
        section_label: The heading to print ABOVE this block, or ``None`` when
            the block continues the previous one's section.  Resolved by the
            assembler rather than in the template because it is a function of
            the ORDER, which the assembler owns; a template deriving it would
            re-read the previous element by index, and index arithmetic over a
            sorted list is how a section heading silently stops appearing.

    :attr:`kind` and :attr:`total` are PROPERTIES rather than fields, and that
    is the same normalization rule this arc applies to the database: both are
    functions of :attr:`purchases` and :attr:`settle`, so storing them beside
    their own inputs would be two values that can come to disagree inside one
    frozen object.
    """

    transaction_id: int
    name: str
    period_start: date
    period_end: date
    purchases: "tuple[OutstandingPurchase, ...]"
    settle: "OutstandingTransaction | None"
    section_label: "str | None"

    @property
    def kind(self) -> OfferKind:
        """Return which section this block belongs in (ruling **R-FC**).

        **A block holding purchases is an ENVELOPE, and that is DERIVED rather
        than assumed.**  Both entry write doors refuse a parent that is not
        purchase-tracked (``entry_service.create_entry`` and ``update_entry``,
        each on ``txn.tracks_purchases``), so "it carries entries" IMPLIES
        envelope-tracked.  The docstring above is careful that no CLAUSE in the
        purchase arm's scope asserts it, and that is still true: the guarantee
        comes from the write door, not from the read.

        A childless block is classified by whether its settle DERIVES its own
        amount -- ``is_correctable`` inverted, which is the settle verb's own
        branch (ruling **R-FF**).  So an envelope whose purchases are all
        already reconciled sits with the envelopes, and an envelope that has
        never had one reads and behaves exactly like a bill, which is what it
        is on this screen: one row with a correctable amount.

        Returns:
            This block's :class:`OfferKind`.
        """
        if self.purchases:
            return OfferKind.ENVELOPE
        if self.settle is not None and not self.settle.is_correctable:
            return OfferKind.ENVELOPE
        return OfferKind.BILL

    @property
    def total(self) -> Decimal:
        """Return the sum of :attr:`purchases`.

        **It is the PURCHASES' total and not the block's**, deliberately: an
        envelope settles at sum(entries), so adding :attr:`settle`'s amount to
        this would report `$200` against `$100` of money for a `$100` envelope
        holding two `$40` / `$60` purchases.  The panel prints this beside the
        heading of a block that HAS children and prints :attr:`settle`'s own
        amount on the settle row; the two are different units and are never
        summed.

        Returns:
            The sum, quantised by the source amounts rather than by any
            arithmetic here.  ``Decimal("0.00")`` for a childless block, which
            the panel does not render.
        """
        return sum(
            (purchase.amount for purchase in self.purchases), Decimal("0.00"),
        )


@dataclass(frozen=True)
class OutstandingSet:
    """What a statement of one civil day could still settle, grouped.

    **There are THREE tallies and deliberately no fourth that sums them**
    (ruling **R-FA**, extended by **R-FD**).  A single ``total`` double-counts
    the moment the close tick joins the purchases: an envelope settles at
    ``sum(entries)``, so counting its two `$40` / `$60` purchases AND its `$100`
    close reports `$200` against `$100` of money.  And a payment and a deposit
    do not sum to anything a reader wants.  Naming each tally for exactly what
    it holds is what stops a later leaf implementing a wrong definition
    literally -- which is what the field named ``total`` here was going to be.

    Attributes:
        groups: One block per parent with something outstanding, ordered by
            kind and then by the oldest offer the block carries (ruling
            **R-FC**).
        purchase_count: How many PURCHASES the set offers -- entries whose
            posting day has not been recorded.  Ticking one moves no money on
            its own; it releases that much of its envelope's reservation.
        purchase_total: The sum of those purchases.
        payment_count: How many EXPENSE rows the set offers -- an envelope's
            own close, or a bill.  Ticking one settles the row.
        payment_total: What those rows would book.
        deposit_count: How many INCOME rows the set offers -- money the
            projection is still waiting to arrive (ruling **R-FD**).
        deposit_total: What those rows would book.

    Counting lives here and not in the template because these are the figures
    the panel's copy pluralises on, and money-adjacent counting belongs on the
    services side of the boundary.
    """

    groups: "tuple[OutstandingGroup, ...]"
    purchase_count: int
    purchase_total: Decimal
    payment_count: int
    payment_total: Decimal
    deposit_count: int
    deposit_total: Decimal

    @classmethod
    def empty(cls) -> "OutstandingSet":
        """Return the set for an account with nothing to reconcile.

        The ROUTE's shape for an account carrying no assertion at all: there is
        no day for anything to be inside of, so the producer is never asked.
        It is a constructor here rather than a literal there because the zeros
        are MONEY, and the services boundary is where money is built (a route
        composing ``Decimal("0.00")`` is the shape ``outstanding_total`` had
        before plan step X-f2-c1, and it is how a caller ends up quantising).
        """
        zero = Decimal("0.00")
        return cls(
            groups=(),
            purchase_count=0, purchase_total=zero,
            payment_count=0, payment_total=zero,
            deposit_count=0, deposit_total=zero,
        )

    @property
    def is_empty(self) -> bool:
        """Return True when the account has nothing outstanding.

        The steady state for a user who reconciles as they go, and the state
        the panel answers with its "nothing is being held back twice" copy
        rather than an empty form.

        **Read off the COUNTS, not off ``groups``**, and the difference is a
        wrong empty state rather than a style point.  This accessor was written
        at plan step X-f2-c1 against ``purchase_count`` alone, with its own
        docstring predicting that the next leaf would add a kind arriving from
        somewhere other than a purchase -- so extending the sum here is that
        prediction coming true, not a rewrite.  ``not self.groups`` remains
        wrong for a different reason each leaf; "nothing to tick" survives all
        of them.
        """
        return (
            self.purchase_count == 0
            and self.payment_count == 0
            and self.deposit_count == 0
        )
