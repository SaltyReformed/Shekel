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
from app.models.statement_match import StatementMatchMember

from ._adapters import supported_sources


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


def matches_by_import(account_id: int) -> "dict[int, list[int]]":
    """Return which accepted matches name a line each of *account_id*'s imports owns.

    **ONE statement, and ONE spelling of the question**, because two callers
    ask it for different reasons and a confirmation that counted differently
    from the act it confirms would be a confirmation that lies:
    :func:`import_history` takes ``len()`` of each list to say what a delete
    would release, and :func:`~._undo.delete_import` iterates the list to
    release them.

    Args:
        account_id: The account whose imports to read.

    Returns:
        ``{import_id: [match_id, ...]}``, each list ascending, covering only
        the imports that own a matched line.  An import with none is absent,
        and both callers read that as the empty list -- which is the honest
        answer rather than an absence to branch on.
    """
    rows = (
        db.session.query(
            BankStatementLine.import_id, StatementMatchMember.match_id,
        )
        .join(
            StatementMatchMember,
            StatementMatchMember.bank_statement_line_id == BankStatementLine.id,
        )
        .filter(BankStatementLine.account_id == account_id)
        .distinct()
        .order_by(BankStatementLine.import_id, StatementMatchMember.match_id)
        .all()
    )
    by_import: "dict[int, list[int]]" = {}
    for import_id, match_id in rows:
        by_import.setdefault(import_id, []).append(match_id)
    return by_import


@dataclass(frozen=True)
class ImportRecord:  # pylint: disable=too-many-instance-attributes
    """One import as the page shows it: what it DID, and what undoing it costs.

    Pylint: too-many-instance-attributes -- **eight because the page's import
    row shows eight things** (8/7), not because the value wants splitting.
    ``StatementLine``, ``CandidateRow``, ``CreatedPurchase`` and
    ``PurchaseDestination`` carry the same disable for the same reason: a row
    that genuinely states N things is not improved by hiding ``N - 7`` of them.

    **The line count on the destructive control is `recorded_count`, and a
    "live" count beside it was DELETED as a guard against an unreachable
    state.**  A first version carried both, on the reasoning that a stored
    figure must not stand in for a live one.  Adversarial review measured the
    premise false: the only things that remove a ``bank_statement_lines`` row
    are the import's own cascade and the account's, so for any import this page
    can render the two are identically equal, always.  CLAUDE.md rule 13
    forbids handling an impossible scenario, and a "live" number that can never
    differ is a claim to freshness the schema does not support.
    :attr:`matches_affected` is genuinely live -- a match can be released
    independently -- and earns its query.

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
    matches_affected: int


def import_history(account_id: int, limit: int = 20) -> "list[ImportRecord]":
    """Return *account_id*'s most recent imports, newest first.

    Args:
        account_id: The account whose imports to list.
        limit: How many to return.  Bounded rather than unbounded because this
            feeds a page section, and an account imported weekly for years
            would otherwise render thousands of rows.
            **The page SAYS SO, and until 2026-08-20 this docstring claimed it
            did while the section heading was the bare word "Imports".**  That
            became load-bearing when plan step ``bank_import:X-f6a-4`` put a
            destructive control inside the truncated table and wrote two
            refusal messages promising it is there: a line names the import
            that FIRST recorded it, so the oldest import owns nearly every line
            and is the first to fall off a newest-first list.  Finding
            **N-330** owns raising or paging the bound; saying it is what stops
            the truncation being silent meanwhile.  Found by adversarial
            financial review 2026-08-20.

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
    by_import = matches_by_import(account_id)
    return [
        ImportRecord(
            import_id=row.id,
            created_at=row.created_at,
            file_name=row.file_name,
            period_start=row.period_start,
            period_end=row.period_end,
            line_count=row.line_count,
            recorded_count=row.recorded_count,
            matches_affected=len(by_import.get(row.id, ())),
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
