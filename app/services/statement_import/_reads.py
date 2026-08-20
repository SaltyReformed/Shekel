"""What has been recorded for an account, for the page that shows it.

Read-only, and separate from :mod:`._record` for the reason every package here
splits that way: the write door and the reader answer different questions, and a
reader living inside the door is a reader nobody can call without one.

Services-boundary discipline: plain data in, frozen dataclasses out, no Flask
import.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.extensions import db
from app.models.ref import StatementSource
from app.models.statement_import import BankStatementLine, StatementImport

from ._adapters import supported_sources
from ._undo import RemovalPreview, removal_previews


@dataclass(frozen=True)
class SourceOption:
    """One adapter a user may actually import with.

    Attributes:
        value: The :class:`~app.enums.StatementSourceEnum` member's value --
            what the form submits and what
            :func:`~._adapters.parse_statement` resolves.
        label: The human-readable name, from ``ref.statement_sources``.
    """

    value: str
    label: str


def available_sources() -> "list[SourceOption]":
    """Return the sources a user may choose, labelled.

    The INTERSECTION of two facts, and it is an intersection deliberately: the
    ref table says which sources the database knows about, and
    :func:`~._adapters.supported_sources` says which ones have a parser.  A row
    seeded ahead of its adapter -- which is exactly what happens when a later
    leaf seeds a source before writing its reader -- must not be offerable, and
    a parser whose ref row is missing must not be silently unlabelled.

    Returns:
        One :class:`SourceOption` per usable source, in enum declaration order.
    """
    labels = dict(
        db.session.query(StatementSource.name, StatementSource.display_name)
        .all()
    )
    return [
        SourceOption(value=member.value, label=labels[member.value])
        for member in supported_sources()
        if member.value in labels
    ]


@dataclass(frozen=True)
class RecordedSpan:
    """What an account's recorded statement lines cover, in total.

    Attributes:
        line_count: How many lines are recorded.
        first_day: The earliest day recorded, or ``None`` when none is.
        last_day: The latest, or ``None``.
        net_amount: The signed sum of every recorded line.  Shown because it is
            the one figure that says at a glance whether a span is plausible;
            it is NOT compared against any app balance here, because comparing
            them is the next leaf's whole subject.
    """

    line_count: int
    first_day: date | None
    last_day: date | None
    net_amount: Decimal


def recorded_span(account_id: int) -> RecordedSpan:
    """Return what *account_id* has recorded, as one aggregate query.

    Args:
        account_id: The account to summarise.

    Returns:
        Its :class:`RecordedSpan`.  An account with nothing recorded gets a
        zero-count span with ``None`` days and a ``$0.00`` net, which is the
        honest answer rather than an absence the caller has to branch on.
    """
    row = (
        db.session.query(
            db.func.count(BankStatementLine.id),
            db.func.min(BankStatementLine.posted_on),
            db.func.max(BankStatementLine.posted_on),
            db.func.coalesce(db.func.sum(BankStatementLine.amount), 0),
        )
        .filter(BankStatementLine.account_id == account_id)
        .one()
    )
    return RecordedSpan(
        line_count=row[0],
        first_day=row[1],
        last_day=row[2],
        net_amount=Decimal(str(row[3])),
    )


@dataclass(frozen=True)
class ImportRecord:  # pylint: disable=too-many-instance-attributes
    """One import as the page shows it: what it DID, and what undoing it costs.

    Pylint: too-many-instance-attributes -- **nine because the page's import row
    shows nine things** (9/7), not because the value wants splitting.  The one
    real seam here is history against live -- the last two fields are read when
    the page renders, the rest are what the act recorded -- and nesting those
    two behind a value still leaves eight, so the split would buy a level of
    indirection in the template and no reduction at all.  ``StatementLine``,
    ``CandidateRow`` and ``CreatedPurchase`` carry the same disable for the same
    reason: a row that genuinely states N things is not improved by hiding
    ``N - 7`` of them.

    **Two counts that look alike and are not the same fact**, which is why both
    are here.  :attr:`recorded_count` is HISTORY -- what this act wrote, on the
    day it ran -- and :attr:`lines_held` is what it still owns, which is what a
    delete would take.  They are equal until something removes a line, and
    putting the historical figure on a destructive control would be a stored
    value standing in for a live one: the substitution this project's arcs
    exist to remove, on the sentence an owner reads before pressing delete.

    Attributes:
        import_id: The act, so a delete control can name it.
        created_at: When it ran.
        file_name: What was uploaded.
        period_start: The earliest day it covered.
        period_end: The latest.
        line_count: Lines the file held.
        recorded_count: Lines this act wrote.  The difference from
            :attr:`line_count` is the overlap with what was already known, and
            showing it is what makes idempotency VISIBLE.
        lines_held: Lines it still owns, counted NOW -- what a delete removes.
        matches_affected: Accepted matches naming at least one of those lines.
            Each would be RELEASED by a delete, so the control says so before
            it is pressed.
    """

    import_id: int
    created_at: datetime
    file_name: str
    period_start: date
    period_end: date
    line_count: int
    recorded_count: int
    lines_held: int
    matches_affected: int


def import_history(account_id: int, limit: int = 20) -> "list[ImportRecord]":
    """Return *account_id*'s most recent imports, newest first.

    Args:
        account_id: The account whose imports to list.
        limit: How many to return.  Bounded rather than unbounded because this
            feeds a page section, and an account imported weekly for years
            would otherwise render thousands of rows; the page says so rather
            than truncating silently.

    Returns:
        One :class:`ImportRecord` per import, newest first.  **Values rather
        than ORM rows** (plan step ``bank_import:X-f6a-4``): the page needs two
        facts that are not columns -- what an import still owns, and what
        undoing it would release -- and a template reaching through a mapped
        row for one and a passed-in map for the other is two shapes for one
        table section.
    """
    imports = (
        db.session.query(StatementImport)
        .filter(StatementImport.account_id == account_id)
        .order_by(StatementImport.created_at.desc(), StatementImport.id.desc())
        .limit(limit)
        .all()
    )
    if not imports:
        return []
    previews = removal_previews(account_id)
    nothing = RemovalPreview(lines=0, matches=0)
    return [
        ImportRecord(
            import_id=row.id,
            created_at=row.created_at,
            file_name=row.file_name,
            period_start=row.period_start,
            period_end=row.period_end,
            line_count=row.line_count,
            recorded_count=row.recorded_count,
            lines_held=previews.get(row.id, nothing).lines,
            matches_affected=previews.get(row.id, nothing).matches,
        )
        for row in imports
    ]


def recent_lines(
    account_id: int, limit: int = 25,
) -> "list[BankStatementLine]":
    """Return *account_id*'s most recently POSTED lines, newest first.

    Ordered by the bank's own day rather than by when they were recorded: the
    question the page answers is "what does my bank say happened lately", and
    an import backfilling an old span should not push older lines to the top.

    Args:
        account_id: The account whose lines to list.
        limit: How many to return.

    Returns:
        The lines, newest posted day first.
    """
    return (
        db.session.query(BankStatementLine)
        .filter(BankStatementLine.account_id == account_id)
        .order_by(
            BankStatementLine.posted_on.desc(),
            BankStatementLine.id.desc(),
        )
        .limit(limit)
        .all()
    )
