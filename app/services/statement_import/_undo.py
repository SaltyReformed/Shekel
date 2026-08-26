"""The ONE door that undoes an import, and the only thing here that DESTROYS.

Recording what a bank said is append-only everywhere else in this package, and
deliberately so: an observation quietly rewritten is what ruling **R-FL** exists
to prevent.  **What that left was a dead end** (finding **N-302**, plan step
``bank_import:X-f6a-4``).  No door in ``app/`` deleted an import, a line or a
recorded account identity, so a single refusal -- a restated line, or a
first import that named the wrong Shekel account -- ended that account's ability
to import for good, while the refusal's own message promised a repair the app
could not perform.  This is that repair.

**It is balance-neutral EXCEPT for what the review CREATED from these lines**
(plan step ``bank_import:X-f6f``, ruling **R-GG**, which amends **R-GB**).
Deleting an import destroys what the BANK said.  The days an accepted match
wrote are the APP's own record and they stay, which is exactly
:func:`~app.services.statement_match.release_match`'s rule: the bank is still
the best evidence the app has about when that money moved, so reverting a
correction in order to tidy a relation would throw away the fact and keep the
bookkeeping.  What comes back is the QUESTION -- those app rows are matchable
again, and the lines are gone.

**A row the review pass CREATED from one of these lines is not the app's own
record**: it exists only because that line did, and destroying the line while
keeping it leaves a movement in the books that nothing accounts for.  So it
goes with the line, through the same release door, and this act therefore MOVES
MONEY where R-GB said it could not.  The receipt says how many rows and how
much (:class:`ImportRemoval`), and the confirmation names them before the
button is pressed -- a destructive act whose report is a single word leaves the
owner unable to tell a no-op from a much larger removal than they meant.

**It reaches only what was recorded AFTER the marker existed, and saying so is
the point.**  An act carries a creation record because the door that made the
row wrote one; the 230 acts already on the developer's database predate that
and carry none, so deleting those imports removes 0 rows rather than the 103
purchases and 47 budget lines the pass built.  **A backfill was considered and
is measured UNSAFE**: the tightest signature available -- one line member, one
entry member, an ``observed`` posting day equal to the line's, and the line's
own figure -- matches **62 purchases the app already had** alongside the 103,
because an accepted match writes exactly those facts onto a row it merely
re-dates and may re-price.  A marker inferred from it would arm this door to
delete the owner's own records, which is the guess ``created_version_id``
exists to replace.  What those 165 rows have instead is the door
``entry_service`` gained in the same step: measured on that database, all 103
are removable by hand and 0 are refused.

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
from decimal import Decimal

from app.exceptions import ValidationError
from app.extensions import db
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.statement_import import BankStatementLine, StatementImport
from app.services.statement_match import release_match

from ._anchor import release_anchors_from
from ._identity import forget_identity_if_last
from ._reads import matches_by_import


@dataclass(frozen=True)
class ImportRemoval:  # pylint: disable=too-many-instance-attributes
    """What undoing one import actually removed.

    Pylint: ``too-many-instance-attributes`` (10/7) -- ten because a delete
    undoes ten distinct things, not because the value wants splitting.  The
    ninth and tenth arrived with plan step ``bank_import:X-f6f`` and they are
    the two that say this act MOVES MONEY; hiding either to satisfy a limit is
    how a destructive receipt comes to under-report what it destroyed.
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
        rows_removed: Rows the review pass had CREATED from those lines and
            which went with them (ruling **R-GG**) -- a purchase a bank line
            became, a recorded difference, or a budget line minted to hold a
            purchase and now holding nothing.
        cash_removed: The signed money the account has stopped recording as a
            result, positive INTO the account.  **A figure and not only a
            count**, because this is the one field on this receipt that says
            the act moved money at all.
        anchors_released: Balance anchors this delete invalidated by removing
            the lines they rested on.  Reported rather than silent because an
            account that had a checked bank balance and now has none is a
            change the owner should see stated, not discover later.
        identity_forgotten: Whether the source-account pairing went too, which
            happens exactly when this was the account's LAST import from that
            source.
        merchants_forgotten: Merchants this account has been left with no
            reason to remember (:func:`_forget_orphan_merchants`).
    """

    import_id: int
    file_name: str
    period_start: date
    period_end: date
    lines_removed: int
    matches_released: int
    anchors_released: int
    identity_forgotten: bool
    merchants_forgotten: int
    rows_removed: int
    cash_removed: Decimal


@dataclass(frozen=True)
class _Doomed:
    """What an import WAS, read while its row still exists.

    Every fact this door's receipt needs about the import itself, taken before
    the delete -- which is the ordering the door's own docstring rests on:
    afterwards the row is gone and an attribute read off a deleted instance is
    a property of the session rather than of the database.

    Attributes:
        source_id: Which adapter recorded it, for the identity reclamation.
        file_name: What it was uploaded as, so the flash names what went.
        period_start: The earliest day it covered.
        period_end: The latest.
    """

    source_id: int
    file_name: str
    period_start: date
    period_end: date

    @classmethod
    def of(cls, statement_import: StatementImport) -> "_Doomed":
        """Return the four facts *statement_import* is about to stop stating.

        Args:
            statement_import: The import this door is about to delete.

        Returns:
            Its :class:`_Doomed`.
        """
        return cls(
            source_id=statement_import.source_id,
            file_name=statement_import.file_name,
            period_start=statement_import.period_start,
            period_end=statement_import.period_end,
        )


def _forget_orphan_merchants(account_id: int) -> int:
    """Delete this account's merchants that nothing has a reason to remember.

    Plan step ``bank_import:X-gd-1``, on an adversarial security review of
    2026-08-25.  A merchant is created by an import and is deliberately NOT
    removed with the lines that named it: a stated answer must stay readable
    and restatable after its lines are gone, which is the property that retired
    ``statable_merchants``' second derivation.  **That reason is about
    ANSWERED merchants, and only those.**  A merchant with no surviving line
    AND no stated answer preserves nothing -- ``merchant_section`` does not
    render it, no rule is keyed on it, and nothing else can reach it.

    **Without this the table has no ceiling at all**, which is precisely the
    hazard ``_rules._refuse_unknown_merchants`` was written for and which
    moved one table over when the rule's key became a foreign key: an owner
    uploading a file naming N unseen merchants and then deleting the import
    reclaims the lines and keeps the merchants, permanently, once per upload.
    The identity reclamation beside it
    (:func:`~._identity.forget_identity_if_last`) makes the same trade for the
    same reason.

    Run AFTER the import and its lines are gone, so *surviving* means what it
    says.

    Args:
        account_id: The account whose merchants to sweep.  Scoped like every
            other read here; a merchant is per-account.

    Returns:
        How many were removed, for the receipt.
    """
    named_by_a_line = (
        db.session.query(BankStatementLine.merchant_id)
        .filter(BankStatementLine.account_id == account_id)
        .filter(BankStatementLine.merchant_id.isnot(None))
    )
    answered_for = (
        db.session.query(MerchantRule.merchant_id)
        .filter(MerchantRule.account_id == account_id)
    )
    return (
        db.session.query(Merchant)
        .filter(
            Merchant.account_id == account_id,
            Merchant.id.notin_(named_by_a_line),
            Merchant.id.notin_(answered_for),
        )
        .delete(synchronize_session=False)
    )


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


def _release_matches(
    import_id: int, owner_id: int, account_id: int,
) -> "tuple[int, int, Decimal]":
    """Release every accepted match naming a line this import owns, and tally.

    **Each goes through the ONE door that releases a match**, which is why this
    package calls up into its sibling at all (see the module docstring).  What
    that door removes is what this function counts: it decides which created
    rows go and which containers stay, and a tally re-derived here would be a
    second answer to a question the act has already answered.

    Args:
        import_id: The import being undone.
        owner_id: The user the route proved owns the account.
        account_id: The account.

    Returns:
        ``(matches_released, rows_removed, cash_removed)``.

    Raises:
        ValidationError: From a release -- a match gone since it was read, or
            one whose created row the owner has edited.  It takes the whole
            delete with it rather than leaving an import half undone.
        PostingError: From reversing a created row's postings.
    """
    # The SAME read the page previews with, so a confirmation cannot count
    # differently from the act it confirms.
    match_ids = matches_by_import(account_id).get(import_id, [])
    released = [
        release_match(match_id, owner_id, account_id)
        for match_id in match_ids
    ]
    return (
        len(match_ids),
        sum(one.removed_rows for one in released),
        sum((one.removed_cash for one in released), Decimal("0.00")),
    )


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
            account, when a match naming its lines has gone since it was read,
            or when one of those matches created a row the owner has EDITED
            since -- that release refuses, and the refusal takes the whole
            delete with it rather than leaving an import half undone.
    """
    statement_import = _owned_import(import_id, owner_id, account_id)
    doomed = _Doomed.of(statement_import)

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
    matches_released, rows_removed, cash_removed = _release_matches(
        import_id, owner_id, account_id,
    )

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

    identity_forgotten = forget_identity_if_last(account_id, doomed.source_id)
    merchants_forgotten = _forget_orphan_merchants(account_id)
    db.session.flush()

    return ImportRemoval(
        import_id=import_id,
        file_name=doomed.file_name,
        period_start=doomed.period_start,
        period_end=doomed.period_end,
        lines_removed=lines_removed,
        matches_released=matches_released,
        identity_forgotten=identity_forgotten,
        merchants_forgotten=merchants_forgotten,
        anchors_released=anchors_released,
        rows_removed=rows_removed,
        cash_removed=cash_removed,
    )
