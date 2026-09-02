"""
Shekel Budget App -- A row's amount ownership, as ONE value rather than two columns.

Ruling **R-FI**'s two states as a TYPE (plan step **X-au-k**): a row either
STATES ITS OWN figure or it DECLARES the relation that prices it, and the shape
that says both at once has no expression here.  Mapped onto
``budget.transactions`` and ``budget.transfers`` by a SQLAlchemy ``composite()``,
so the pair those tables store is a single mapped attribute a caller assigns
whole.

**Why a type and not two columns with a CHECK.**  The CHECK
(``ck_transactions_amount_ownership`` / ``ck_transfers_amount_ownership``) has
always been a biconditional, so a half-write has always been refused -- but it
was refused at FLUSH, after the unit of work was built, and any code anywhere
could write one column and leave the other.  ``app.services.amount_ownership``
answered that by being the one door callers were asked to use, which is a
census: it had to be re-run every time the derived population grew, and the two
sites most able to break it were the two a census cannot see
(``recurrence_engine/_maintain.py`` and ``routes/transactions/mutations.py``
splat ``setattr(row, field, value)`` over a VARIABLE field name).  With one
mapped attribute there is no second column to leave behind, and the shape the
CHECK refuses is not constructible.

**This type admits EXACTLY the two legal shapes.**  A row either states a
figure and no relation, or a relation and no figure; a figure beside a
relation is the stale derived amount ruling R-FI deletes, and an empty pair is
a row that has stated no ownership at all.  Both are refused at construction.

**"No ownership stated" is spelled as ``None`` on the attribute, not as an
empty instance of this class**, and the mapping is what makes that work:
:func:`from_columns` is the composite's constructor, so SQLAlchemy answers
``None`` for a row whose two columns are both NULL and never asks this class to
represent a state it has no member for.  That indirection is load-bearing
rather than stylistic -- a composite's ``get_history`` builds its class from
the raw column values of an attribute that may never have been set
(``sqlalchemy/orm/descriptor_props.py``), and ``Session.is_modified`` does the
same on a pending row, so a validating class handed those values DIRECTLY
raises from inside machinery no caller entered.  A first version of this module
did exactly that and had to weaken the type to survive it; the factory keeps
the type total and absorbs the empty pair where it belongs.  Measured on
SQLAlchemy 2.0.49.

The DATABASE still refuses the empty pair at INSERT -- that is what
``ck_transactions_amount_ownership`` is for, and it is the tier that sees a
writer which is not this application at all.

**An illegal pair already in the DATABASE makes the row unreadable, and that is
deliberate.**  The composite is built in SQLAlchemy's ``load`` handler, so a
``SELECT`` touching such a row raises here instead of yielding an object whose
two halves contradict each other.  The CHECK makes it unreachable from this
application; what could produce one is a migration, a ``psql`` session or a
trigger, which is exactly the surface no Python-side structure reaches, and a
row whose ownership is self-contradictory is not a row a budget should price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class AmountOwnership:
    """WHERE a row's plan amount comes from: its own figure, or a relation.

    Frozen, so re-pricing a row REPLACES its ownership rather than editing it
    in place -- which is what makes the whole pair one assignment and what
    lets SQLAlchemy see the change.  Construct through :meth:`own` or
    :meth:`derived`; the bare constructor is TOTAL over the two legal shapes
    and refuses everything else, so there is no instance of this class that a
    row may not carry.

    Attributes:
        figure: The amount this row states as its own, or ``None`` when its
            amount is derived.
        source_id: The ``ref.amount_sources`` id naming the relation that
            prices this row, or ``None`` when the row owns its figure.
            An id rather than an
            :class:`~app.enums.AmountSourceEnum` member because this is the
            storage shape; ``app.services.amount_ownership`` is where a
            member is resolved through ``ref_cache``, so the model layer
            reads no cache.
    """

    figure: Decimal | None
    source_id: int | None

    def __post_init__(self) -> None:
        """Refuse every shape but ruling R-FI's two.

        Raises:
            ValueError: When both halves are set -- the stale derived figure
                this arc exists to delete -- or when neither is, which is a
                row that has stated no ownership and is spelled ``None`` on
                the attribute rather than as an instance of this class.
        """
        if (self.figure is None) == (self.source_id is None):
            raise ValueError(
                "a row states its OWN figure or the relation that prices it, "
                f"never both and never neither: got figure {self.figure!r} "
                f"beside source {self.source_id!r}"
            )

    @classmethod
    def own(cls, figure: Decimal) -> AmountOwnership:
        """Return the ownership of a row that STATES *figure* as its own.

        Rule 1 of ruling R-FI, as the act a door performs when a HUMAN authors
        a figure or when the money has already moved.

        Args:
            figure: The amount the row now states as its own.

        Returns:
            The ownership to assign to the row's ``amount_ownership``.

        Raises:
            ValueError: When *figure* is ``None``.  A row that owns its amount
                has one; the caller meaning "this row is derived" wants
                :meth:`derived`, and the caller meaning "not stated yet" wants
                to leave the attribute alone so the CHECK answers it.
        """
        if figure is None:
            raise ValueError(
                "own() needs a figure; a row with neither a figure nor a "
                "relation is refused by ck_transactions_amount_ownership"
            )
        return cls(figure=figure, source_id=None)

    @classmethod
    def derived(cls, source_id: int) -> AmountOwnership:
        """Return the ownership of a row PRICED BY the relation *source_id*.

        Rule 2 of ruling R-FI: the row names the relation that prices it and
        holds no figure at all, so a stale derived amount is unrepresentable
        rather than merely unlikely.

        Args:
            source_id: The ``ref.amount_sources`` id of the relation, from
                ``ref_cache.amount_source_id``.

        Returns:
            The ownership to assign to the row's ``amount_ownership``.

        Raises:
            ValueError: When *source_id* is ``None``, for the reason
                :meth:`own` states about its own argument.
        """
        if source_id is None:
            raise ValueError(
                "derived() needs the relation that prices the row; a row with "
                "neither a figure nor a relation is refused by "
                "ck_transactions_amount_ownership"
            )
        return cls(figure=None, source_id=source_id)

    def __composite_values__(self) -> tuple[Decimal | None, int | None]:
        """Return the two column values, in the order ``composite()`` maps them.

        SQLAlchemy's composite protocol.

        Returns:
            The figure and the source id, in that order.
        """
        return (self.figure, self.source_id)


def from_columns(figure: Decimal | None,
                 source_id: int | None) -> AmountOwnership | None:
    """Return the ownership those two column values mean, or ``None``.

    **The composite's constructor**, and the reason
    :class:`AmountOwnership` can be total over R-FI's two states.  SQLAlchemy
    builds a composite from raw column values in places no caller enters --
    ``get_history`` for the pre-change side of an attribute that may never have
    been set, and ``Session.is_modified`` on a pending row -- and both hand it
    ``(None, None)``.  Answering ``None`` there keeps the empty pair out of the
    type and leaves it where it is actually decided: the database CHECK, at the
    INSERT.

    Args:
        figure: The row's stored figure column, or ``None``.
        source_id: The row's stored ``amount_source_id`` column, or ``None``.

    Returns:
        The :class:`AmountOwnership` those values state, or ``None`` when the
        row has stated no ownership at all.

    Raises:
        ValueError: When the two columns hold a figure AND a relation.  Reached
            only for a row written around this application -- the CHECK forbids
            it -- and raising is deliberate: a row whose two halves contradict
            each other is not a row a budget may price.
    """
    if figure is None and source_id is None:
        return None
    return AmountOwnership(figure, source_id)
