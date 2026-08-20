"""The ONE door that undoes an import, and the only thing here that DESTROYS.

Recording what a bank said is append-only everywhere else in this package, and
deliberately so: an observation quietly rewritten is what ruling **R-FL** exists
to prevent.  **What that left was a dead end** (finding **N-302**, plan step
``bank_import:X-f6a-4``).  No door in ``app/`` deleted an import, a line or a
recorded account identity, so a single refusal -- a restated line, or a
first import that named the wrong Shekel account -- ended that account's ability
to import for good, while the refusal's own message promised a repair the app
could not perform.  This is that repair.

**It is BALANCE-NEUTRAL, and that is a property rather than a hope.**  Deleting
an import destroys what the BANK said.  The days an accepted match wrote are the
APP's own record and they stay, which is exactly
:func:`~app.services.statement_match.release_match`'s rule: the bank is still
the best evidence the app has about when that money moved, so reverting a
correction in order to tidy a relation would throw away the fact and keep the
bookkeeping.  What comes back is the QUESTION -- those app rows are matchable
again, and the lines are gone.

**A match is RELEASED before its lines are removed, never orphaned.**  A match
act with no bank line left asserts nothing about a bank:
:func:`~app.services.statement_match._accepted_view.accepted_groups` cannot
render one, so no release button ever exists for it, while
:func:`~app.services.statement_match.matched_subjects` reads the member rows
directly and goes on reporting its transactions as already matched -- which
takes those rows out of every future proposal, permanently and invisibly.
Measured on a production clone 2026-08-20, deleting an import by hand left an
act standing with 0 line members and 1 transaction member.
``fk_statement_match_members_line_account`` now refuses to produce that state at
all, so this ordering is CHECKED rather than merely observed.

**Why this calls up into the match package.**  The two service packages are
siblings and neither imported the other; ``statement_match`` reads only
``app.models.statement_import``.  The alternative was for the ROUTE to release
the matches and then call this door, which puts the ordering that the whole
guarantee rests on in the one place it can be forgotten -- and for a second
function to learn how a match is released, when *there is exactly one place a
match is released* is what rulings **R-FT** and **R-FV** ask for.  So the
undo owns the whole act and calls the one door that already exists.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no ``flask`` / ``request`` / ``session`` /
``current_app`` import.  It MUTATES and does NOT commit -- the route owns the
unit of work, which is what makes a refusal here leave nothing behind.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.statement_match import StatementMatchMember
from app.services.statement_match import release_match
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_IMPORT_DELETED,
    log_event,
)

from ._identity import forget_identity_if_last

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportRemoval:
    """What undoing one import actually removed.

    Every field is COUNTED as the act ran rather than read back afterwards,
    because afterwards the rows are gone.  The page reports these, and the
    reporting is the point: a destructive act that says only "done" leaves the
    owner unable to tell a no-op from a much larger removal than they meant.

    Attributes:
        import_id: The act that was undone.
        file_name: What it was uploaded as, so the flash names what went.
        period_start: The earliest day it covered.
        period_end: The latest.
        lines_removed: Bank lines this import had FIRST recorded, and which
            went with it.  A line a later import merely re-saw is one this
            import owns; a line THIS import merely re-saw belongs to an earlier
            one and stays.
        matches_released: Accepted matches that named at least one of those
            lines.  Each is released whole, so a match spanning two imports
            frees the other import's lines back to unexplained as well.
        identity_forgotten: Whether the source-account pairing went too, which
            happens exactly when this was the account's LAST import from that
            source.
    """

    import_id: int
    file_name: str
    period_start: date
    period_end: date
    lines_removed: int
    matches_released: int
    identity_forgotten: bool


def _owned_import(
    import_id: int, owner_id: int, account_id: int,
) -> StatementImport:
    """Return the import, or refuse.

    Args:
        import_id: The act to undo.
        owner_id: The user the route proved owns the account.
        account_id: The account it must belong to.

    Returns:
        The :class:`~app.models.statement_import.StatementImport`.

    Raises:
        ValidationError: When *import_id* names no import on this owner's
            account -- the set-operation form of the project's "404 for both
            not-found and not-yours" rule, raised rather than ignored because
            this door names ONE act on purpose.
    """
    statement_import = (
        db.session.query(StatementImport)
        .filter(
            StatementImport.id == import_id,
            StatementImport.account_id == account_id,
            StatementImport.user_id == owner_id,
        )
        .one_or_none()
    )
    if statement_import is None:
        raise ValidationError(
            "That import is no longer there.  Reload the page; nothing was "
            "changed."
        )
    return statement_import


@dataclass(frozen=True)
class RemovalPreview:
    """What deleting one import WOULD remove, as of now.

    An ESTIMATE, and named as one: it is read when the page renders and the
    act's own :class:`ImportRemoval` is read as the act runs, so a second
    session working the same account between the two can legitimately move
    either number.  What must never differ is the QUESTION the two ask, which
    is why both start from :func:`_owned_lines`.

    Attributes:
        lines: Bank lines the import still owns.
        matches: Accepted matches naming at least one of them.
    """

    lines: int
    matches: int


def _owned_lines(account_id: int):
    """Return the query both line counts and the match join start from.

    Stated once because a confirmation that counts differently from the act it
    confirms is a confirmation that lies, and the drift would be invisible: two
    correct-looking filters, one of them missing the account scope.

    Args:
        account_id: The account whose recorded lines to read.

    Returns:
        The unexecuted query over that account's :class:`BankStatementLine`
        rows.
    """
    return db.session.query(BankStatementLine).filter(
        BankStatementLine.account_id == account_id,
    )


def _matches_naming(import_id: int, account_id: int) -> "list[int]":
    """Return the accepted matches that name a line *import_id* owns.

    ONE statement over the join rather than a load of every line: the question
    is which ACTS are affected, and an account with a year of history holds
    hundreds of lines and a handful of matches.

    Args:
        import_id: The import whose lines to follow.
        account_id: The account, so the read is scoped by the same fact the
            door checked ownership with.

    Returns:
        The distinct match ids, ascending.
    """
    return [
        row[0]
        for row in _owned_lines(account_id)
        .join(
            StatementMatchMember,
            StatementMatchMember.bank_statement_line_id == BankStatementLine.id,
        )
        .filter(BankStatementLine.import_id == import_id)
        .with_entities(StatementMatchMember.match_id)
        .distinct()
        .order_by(StatementMatchMember.match_id)
        .all()
    ]


def removal_previews(account_id: int) -> "dict[int, RemovalPreview]":
    """Return what deleting each of *account_id*'s imports would remove.

    ONE statement for the whole page rather than two per row.  The outer join
    is what keeps an import with no matched line in the answer: it still owns
    lines, and an import missing from this map would render a delete control
    claiming to remove nothing.

    Args:
        account_id: The account whose imports the page is listing.

    Returns:
        ``{import_id: RemovalPreview}``, covering every import that still owns
        at least one line.  An import owning none is absent, and the caller
        supplies the empty preview -- which is the honest reading, because an
        import that recorded nothing new removes nothing when it goes.
    """
    rows = (
        _owned_lines(account_id)
        .outerjoin(
            StatementMatchMember,
            StatementMatchMember.bank_statement_line_id == BankStatementLine.id,
        )
        .with_entities(
            BankStatementLine.import_id,
            db.func.count(db.distinct(BankStatementLine.id)),
            db.func.count(db.distinct(StatementMatchMember.match_id)),
        )
        .group_by(BankStatementLine.import_id)
        .all()
    )
    return {
        row[0]: RemovalPreview(lines=row[1], matches=row[2]) for row in rows
    }


def delete_import(
    import_id: int, owner_id: int, account_id: int,
) -> ImportRemoval:
    """Undo one import: its lines, the matches naming them, and its pairing.

    The order is the guarantee.  Every fact the report needs is read while the
    rows still exist; the affected matches are RELEASED, each through the one
    door that releases a match; only then is the import removed, taking the
    lines it first recorded with it; and the source-account pairing is
    reconsidered afterwards, because whether it survives depends on what is
    left.

    Does NOT commit -- the route owns the session boundary.

    Args:
        import_id: The act to undo.
        owner_id: The user the route proved owns the account.
        account_id: The account it must belong to.

    Returns:
        The :class:`ImportRemoval`.

    Raises:
        ValidationError: When *import_id* names no import on this owner's
            account, or when a match naming its lines has gone since it was
            read.
    """
    statement_import = _owned_import(import_id, owner_id, account_id)
    source_id = statement_import.source_id
    file_name = statement_import.file_name
    period_start = statement_import.period_start
    period_end = statement_import.period_end

    lines_removed = (
        _owned_lines(account_id)
        .filter(BankStatementLine.import_id == import_id)
        .count()
    )
    match_ids = _matches_naming(import_id, account_id)
    for match_id in match_ids:
        release_match(match_id, owner_id, account_id)

    # The lines go with the import at the database tier
    # (``fk_bank_statement_lines_import_account``), which is also what makes
    # the ordering above provable: with the matches gone, nothing names a line,
    # and ``fk_statement_match_members_line_account`` would refuse this
    # statement if anything did.
    db.session.delete(statement_import)
    db.session.flush()

    identity_forgotten = forget_identity_if_last(account_id, source_id)
    db.session.flush()

    removal = ImportRemoval(
        import_id=import_id,
        file_name=file_name,
        period_start=period_start,
        period_end=period_end,
        lines_removed=lines_removed,
        matches_released=len(match_ids),
        identity_forgotten=identity_forgotten,
    )
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_IMPORT_DELETED, BUSINESS,
        "A recorded statement import was deleted.",
        user_id=owner_id, account_id=account_id, import_id=import_id,
        file_name=file_name,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        lines_removed=lines_removed,
        matches_released=len(match_ids),
        identity_forgotten=identity_forgotten,
    )
    return removal
