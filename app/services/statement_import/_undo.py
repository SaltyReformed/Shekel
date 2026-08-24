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

**It does not LOG the act either, and that is the same boundary.**  A business
event asserting "an import was deleted, its lines are gone, N matches were
released" must not sit in the log when the transaction that would have done it
failed -- and this is the door where a false entry costs most, because the log
is the forensic record of the only thing in this package that DESTROYS.  So the
route emits it after its commit, exactly as ``record_statement``'s own event is
emitted by its route rather than by the service.  A first version logged here,
one call above a commit that can still roll back; found by adversarial
financial review 2026-08-20.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no ``flask`` / ``request`` / ``session`` /
``current_app`` import.  It MUTATES and does NOT commit -- the route owns the
unit of work, which is what makes a refusal here leave nothing behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_import import BankStatementLine, StatementImport
from app.services.statement_match import release_match

from ._anchor import release_anchors_from
from ._identity import forget_identity_if_last
from ._reads import matches_by_import


@dataclass(frozen=True)
class ImportRemoval:  # pylint: disable=too-many-instance-attributes
    """What undoing one import actually removed.

    Pylint: ``too-many-instance-attributes`` (8/7) -- eight because a delete
    undoes eight distinct things, not because the value wants splitting.
    ``ImportRecord`` in :mod:`._reads` carries the identical disable for the
    identical reason: a receipt that genuinely states N facts is not improved
    by hiding ``N - 7`` of them from the person who pressed a destructive
    button.

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
        anchors_released: Balance anchors this delete invalidated by removing
            the lines they rested on.  Reported rather than silent because an
            account that had a checked bank balance and now has none is a
            change the owner should see stated, not discover later.
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
    anchors_released: int
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

    # Counted, and the EARLIEST day among them taken, before the cascade
    # removes them: what an anchor rests on is the lines themselves, so the
    # release below is keyed on the days that actually go rather than on this
    # import's declared span.  An import that recorded NOTHING removes nothing
    # and must therefore release nothing -- measured on the developer's own
    # database, where undoing a re-import of his 2026-08-16 export took a good
    # anchor with it while deleting 0 lines.
    lines_removed, earliest_removed = (
        db.session.query(
            db.func.count(BankStatementLine.id),
            db.func.min(BankStatementLine.posted_on),
        )
        .filter(
            BankStatementLine.import_id == import_id,
            BankStatementLine.account_id == account_id,
        )
        .one()
    )
    # The SAME read the page previews with, so a confirmation cannot count
    # differently from the act it confirms.
    match_ids = matches_by_import(account_id).get(import_id, [])
    for match_id in match_ids:
        release_match(match_id, owner_id, account_id)

    # The lines go with the import at the database tier
    # (``fk_bank_statement_lines_import_account``), which is also what makes
    # the ordering above provable: with the matches gone, nothing names a line,
    # and ``fk_statement_match_members_line_account`` would refuse this
    # statement if anything did.
    db.session.delete(statement_import)
    db.session.flush()

    # **Every anchor this delete undercut goes with the lines**, because an
    # anchor is a conclusion drawn from lines at or before its own day and
    # those lines have just gone.  Before this, a later overlapping import kept
    # its span and its anchor while the evidence beneath it vanished, so the
    # coverage test reported "covered" over a `$150.00` hole -- reproduced
    # through these very doors by an adversarial review, 2026-08-23.  Run
    # AFTER the delete so the released set is measured against what survives.
    anchors_released = (
        0 if earliest_removed is None
        else release_anchors_from(account_id, earliest_removed)
    )
    db.session.flush()

    identity_forgotten = forget_identity_if_last(account_id, source_id)
    db.session.flush()

    return ImportRemoval(
        import_id=import_id,
        file_name=file_name,
        period_start=period_start,
        period_end=period_end,
        lines_removed=lines_removed,
        matches_released=len(match_ids),
        identity_forgotten=identity_forgotten,
        anchors_released=anchors_released,
    )
