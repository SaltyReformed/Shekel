"""
Shekel Budget App -- The ONE writer of a row's amount-ownership pair.

Ruling **R-FI**'s two states, as the two acts that put a row into them: a row
either STATES ITS OWN figure or it DECLARES the relation that prices it, and the
two columns that say which are written TOGETHER or not at all.

**It is the write half of :mod:`app.services.cash_ledger._amount_source`**,
which is the read half.  That module answers *where does this row's amount come
from* and never writes; this one moves a row between the two states and never
prices anything.  Splitting them by direction is why the read package can state
"no writes" as a boundary and mean it.

**Why a function rather than two assignments at each door.**
``ck_transactions_amount_ownership`` and ``ck_transfers_amount_ownership`` are
BICONDITIONALS -- ``(amount_source_id IS NULL) = (<figure> IS NOT NULL)`` -- so
every legal write moves BOTH columns and every half-write is an
``IntegrityError`` at flush.  A door that writes one column is not a door with a
bug in it; it is a door that cannot commit.  Stating the pair once means no
caller can express the half-write at all, which is the difference between a
constraint that catches a mistake and a shape that cannot make it.

**The figure column differs by table and the registry below is why this is not
two modules.**  A transaction stores an owned figure in ``estimated_amount`` and
a transfer in ``amount``; everything else about the pair is identical, and the
CHECK on each table is the same sentence.  A mapping keyed on the model -- the
same shape ``_amount_source._RULE_ANSWERS`` uses, and for the same reason --
means a THIRD table brought under the amount model raises at the lookup instead
of silently taking whichever branch an ``if`` chain happened to end on.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): ORM rows in, nothing
out.  Mutates the rows it is given in place; issues no query, does not flush and
does not commit, so a caller's unit of work is unchanged.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import AmountSourceEnum
from app.models.transaction import Transaction
from app.models.transfer import Transfer

#: WHICH COLUMN each table stores an OWNED figure in.  The two tables ruling
#: R-FI's CHECK covers, and the only two: a model absent here raises
#: :class:`KeyError` at :func:`_figure_column` rather than being written
#: through some default.  ``tests/test_services/test_amount_ownership.py``
#: grades this against the models that actually carry ``amount_source_id``, so
#: the completeness is a predicate rather than a comment.
_FIGURE_COLUMNS = {
    Transaction: "estimated_amount",
    Transfer: "amount",
}


def _figure_column(row) -> str:
    """Return the name of the column *row*'s table stores an owned figure in.

    Args:
        row: A :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`.

    Returns:
        ``"estimated_amount"`` or ``"amount"``.

    Raises:
        KeyError: When *row* is of a model this seam has no figure column for.
            That is a table brought under ``amount_source_id`` without being
            registered above, and failing here is how it fails loudly instead of
            being written into a state its own CHECK refuses.
    """
    return _FIGURE_COLUMNS[type(row)]


def state_own_amount(row, figure: Decimal) -> None:
    """Make *row* OWN *figure*: store it, and clear the relation that priced it.

    The act a door performs when a HUMAN authors a figure.  Rule 1 of ruling
    R-FI -- ``amount_source_id IS NULL`` beside a stored amount -- and the
    statement :func:`app.services.cash_ledger.amount_rule` reads back: *a row a
    human re-priced owns its figure because the write door CLEARS its source*.

    **Clearing the source is not optional and is not defensive.**  The
    ownership CHECK pairs the two columns one-to-one, so storing a figure while
    a relation still claims the row is an ``IntegrityError`` at flush rather
    than a stale number nobody can date.

    Idempotent: a row that already owns a figure is simply restated, which is
    what lets a door call this without first asking which state the row is in.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` being re-priced.
        figure: The amount the row now states as its own.

    Raises:
        KeyError: See :func:`_figure_column`.
    """
    setattr(row, _figure_column(row), figure)
    row.amount_source_id = None


def declare_derived(row, relation: AmountSourceEnum) -> None:
    """Declare *row*'s amount DERIVED by *relation*, and empty its figure.

    The act a per-kind cutover performs when a row stops being priced by a
    stored copy: the row names the RELATION that prices it -- its recurring
    definition, or its parent transfer -- and holds no figure at all, so a
    stale derived amount becomes unrepresentable rather than merely unlikely
    (ruling **R-FI**, plan step X-au-c1).

    **It names the RELATION and never the RULE**, which is ruling **R-FK**:
    whether a definition is salary-linked, or a parent transfer is a loan
    payment, is a property of the DEFINITION read live at price time, so a mode
    flip changes what the row is worth without rewriting the row.  That is why
    a transfer SHADOW takes ``PARENT_TRANSFER`` whether or not the transfer is
    a loan payment, and why the loan-settings routes need no writer of their
    own.

    Idempotent, and that is load-bearing rather than incidental: the transfer
    door re-declares both legs on every definition-driven amount write, so a
    leg that is already derived must be a no-op and a leg an owner had taken is
    handed back to its parent by the same call.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` being declared.
        relation: Which :class:`~app.enums.AmountSourceEnum` relation prices it.

    Raises:
        KeyError: See :func:`_figure_column`, or when *relation* is not a
            seeded member (``ref_cache.amount_source_id``).
    """
    setattr(row, _figure_column(row), None)
    row.amount_source_id = ref_cache.amount_source_id(relation)


def owns_its_amount(row) -> bool:
    """Return whether *row* states its own figure rather than deriving it.

    The NULL test the ownership CHECK is written over, named once so a caller
    asks the question rather than restating the encoding.  ``True`` means rule
    1 prices this row; ``False`` means its ``amount_source_id`` names the
    relation that does.

    **Nothing here reads ``is_override``, ``is_projected`` or ``is_deleted``**,
    which is finding **N-262**'s rule: those three say whether a row COUNTS and
    who last touched it, never who owns its figure.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` to ask about.

    Returns:
        ``True`` when the row owns its amount.
    """
    return row.amount_source_id is None
