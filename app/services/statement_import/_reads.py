"""What has been recorded for an account, for the page that shows it.

Read-only, and separate from :mod:`._record` for the reason every package here
splits that way: the write door and the reader answer different questions, and a
reader living inside the door is a reader nobody can call without one.

Services-boundary discipline: plain data in, frozen dataclasses out, no Flask
import.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import StatementSource
from app.models.statement_import import BankStatementLine, StatementImport

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


def import_history(account_id: int, limit: int = 20) -> "list[StatementImport]":
    """Return *account_id*'s most recent imports, newest first.

    Args:
        account_id: The account whose imports to list.
        limit: How many to return.  Bounded rather than unbounded because this
            feeds a page section, and an account imported weekly for years
            would otherwise render thousands of rows; the page says so rather
            than truncating silently.

    Returns:
        The imports, newest first, with their source eagerly loaded.
    """
    return (
        db.session.query(StatementImport)
        .filter(StatementImport.account_id == account_id)
        .order_by(StatementImport.created_at.desc(), StatementImport.id.desc())
        .limit(limit)
        .all()
    )


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
