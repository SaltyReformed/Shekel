"""The ONE door that records what a statement said.

Everything it can refuse, it refuses BEFORE it INSERTS a row: the file is
parsed, its running-balance chain verified, its account identity reconciled and
its lines compared against what is already recorded, and only then is anything
staged.  So a refused import writes no new line without depending on the
rollback.

**The claim is about INSERTS and not about the session, and a wider version of
it was left standing here once.**  The reconciliation walks group by group and
:func:`_absorb_gained_facts` fills a recorded row's NULLs as it goes, so a
refusal raised on group *k* leaves groups 1..*k*-1 dirty in the session and it
is the route's rollback that discards them.  Nothing is lost by that -- those
writes only ever ADD a fact the file states -- but "leaves the database exactly
as it was without depending on the rollback" was true of the old per-line loop
and is not true of this one.  Found by adversarial financial review 2026-08-20.

**Nothing here moves a figure.**  Recording what a bank said is separable from
deciding which of the app's own rows it explains, and that separation is the
leaf boundary (plan step ``bank_import:X-f6a``): the match, the review and the
``settled_on`` correction are the leaf after this one.  A reader can therefore
grade this commit by a property rather than by inspection -- no balance moves,
because no balance input is written.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no ``flask`` / ``request`` / ``session`` /
``current_app`` import.  It FLUSHES and does not commit, matching
``entry_service``'s doors: the route owns the unit of work.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatementSourceEnum
from app.exceptions import StatementLineConflict
from app.extensions import db
from app.models.statement_import import BankStatementLine, StatementImport

from ._adapters import parse_statement
from ._identity import record_identity, verify_identity
from ._integrity import closing_balance, opening_balance, verify_running_balance
from ._line import (
    KeyedLine,
    StatementLine,
    fresh_ordinals,
    group_indexes,
    group_key,
    pair_by_statement,
)


@dataclass(frozen=True)
class ImportOutcome:
    """What one import act did.

    Attributes:
        import_id: The ``budget.statement_imports`` row recording the act.
        line_count: Lines the file held.
        recorded_count: Lines this import wrote.
        period_start: The earliest day the file covers.
        period_end: The latest.
        opening_balance: The balance before the first line, where the source
            carries a running balance.
        closing_balance: The balance after the last, likewise.
    """

    import_id: int
    line_count: int
    recorded_count: int
    period_start: date
    period_end: date
    opening_balance: Decimal | None
    closing_balance: Decimal | None

    @property
    def already_known(self) -> int:
        """Return how many of the file's lines were already recorded.

        DERIVED rather than stored, which is this project's own rule about
        derived values applied to its own return type: two fields that must sum
        to a third are two fields that can come to disagree.  It is named at
        all -- rather than left to the caller's subtraction -- because it is
        the number that makes idempotency VISIBLE, and a caller doing the
        arithmetic itself is a caller that can do it backwards.
        """
        return self.line_count - self.recorded_count


def _recorded_groups(
    account_id: int, period_start: date, period_end: date,
) -> "dict[tuple[date, Decimal], list[BankStatementLine]]":
    """Return this account's already-recorded lines over the file's span.

    Loaded as ONE query over the day range rather than one per line: a full
    year's export is ~360 lines, and a per-line existence check would be 360
    round trips to answer a question one indexed range scan answers.
    ``idx_bank_statement_lines_account_day`` is the index it uses.

    **Grouped by ``(posted_on, amount)`` rather than keyed by the full
    identity**, which is what makes the reconciliation set-wise: the recorded
    ordinal is a surrogate this app assigned, so a group is looked up by what
    the BANK stated and the members inside it are then paired on their wording
    (:func:`~._line.pair_by_statement`).

    Args:
        account_id: The account being imported into.
        period_start: The earliest day the file covers.
        period_end: The latest.

    Returns:
        The recorded lines grouped by the day and amount they share, each
        group ordered by its own ordinal so the pairing walks a stable
        sequence.
    """
    rows = (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.posted_on >= period_start,
            BankStatementLine.posted_on <= period_end,
        )
        .order_by(BankStatementLine.sequence_in_group)
        .all()
    )
    groups: "dict[tuple[date, Decimal], list[BankStatementLine]]" = defaultdict(
        list,
    )
    for row in rows:
        groups[group_key(row.posted_on, row.amount)].append(row)
    return dict(groups)


def _refuse_restatement(
    line: StatementLine, recorded: BankStatementLine,
) -> None:
    """Refuse when a recorded line's own DESCRIPTION is restated.

    A statement line is an OBSERVATION, and an observation quietly rewritten is
    what ruling **R-FL** exists to prevent -- so the fact the source states
    ABOUT THE LINE must agree with what is already recorded.

    **What reaches this is a group whose incoming and recorded halves BOTH have
    a member the other cannot account for** (:attr:`~._line.GroupPairing
    .restates`), which is the only shape that is a contradiction rather than a
    change in what the export covers.  It used to be reached by a positional
    compare, and that fired on two events the bank had not restated at all: a
    re-ordered pair of same-day same-amount lines, and a genuinely new line the
    bank inserted ahead of a recorded one.

    **The running balance is deliberately NOT compared, and that is a measured
    correction rather than a relaxation.**  A running balance is not a fact
    about a line at all: it is a prefix sum over the bank's LISTING ORDER, and
    SECU lists a day's card debits sorted by ascending magnitude rather than by
    arrival.  So a card swipe that finalizes onto a day already listed is
    INSERTED into that day's block, not appended, and every later line on that
    day legitimately gets a different running balance -- while both files
    verify their own chain perfectly.  Comparing it per line refused an honest,
    more-complete re-export of the user's own year-to-date statement, named the
    bank as having restated something it had not, and left that account unable
    to import ever again.  The in-file chain check
    (:func:`~._integrity.verify_running_balance`) is where a balance is graded;
    once recorded it is provenance, and :func:`_absorb_gained_facts` keeps it
    current.

    **The two lines it names are EXAMPLES, not a pairing.**
    :func:`~._line.pair_by_statement` declined to pair them -- that is what
    makes them leftovers -- so with three unaccounted-for incoming lines and
    two unclaimed recorded ones there is no correspondence to state, and a
    message asserting one would be a true sentence about the wrong problem.
    The wording therefore says what the code knows: the file states this, the
    app holds that, at this day and amount.  A first version read "was already
    recorded as X and this file states Y", which asserts a pairing; found by
    adversarial design review 2026-08-20.

    Args:
        line: One incoming line the file states and the app cannot account for.
        recorded: One recorded line in the same group the file no longer
            states.

    Raises:
        StatementLineConflict: Always.  The caller has already established that
            this group restates something, so a guard here would be a second
            copy of that decision.
    """
    raise StatementLineConflict(
        line.posted_on, line.amount,
        recorded.description, line.description,
    )


def _absorb_gained_facts(
    line: StatementLine, recorded: BankStatementLine,
) -> None:
    """Fill in what a later export states and the recorded row does not.

    **Information GAINED is not a restatement**, and until this existed it was
    silently discarded -- on the path the user actually takes.  The
    running-balance column is an export OPTION: the developer imported the
    10-column file first, was told by the page to re-export with the column,
    and the second import recognised every line as already known and threw the
    balances away, leaving the column NULL forever on the very fact the CSV was
    chosen over the OFX to obtain.

    Only ``NULL`` is filled.  A value that DISAGREES is left alone rather than
    overwritten, for the reason :func:`_refuse_restatement` no longer compares
    it: the later file is not more authoritative about a figure that depends on
    listing order.  **That rule is what makes the ``transaction_on`` arm safe
    to add**: a stated transaction day is an observation, so a second export
    restating it differently is a disagreement to leave alone rather than a
    correction to apply.

    Args:
        line: The incoming line the file states.
        recorded: The line already held, paired to it by wording.
    """
    if recorded.running_balance is None and line.running_balance is not None:
        recorded.running_balance = line.running_balance
    if recorded.source_category is None and line.source_category:
        recorded.source_category = line.source_category
    if recorded.external_id is None and line.external_id:
        recorded.external_id = line.external_id
    # **The transaction day joins the same rule at plan step X-f6a-3a**, and
    # it is the one that MOVES A DATE rather than adding provenance: a match
    # writes this day onto a matched purchase's ``purchased_on`` (ruling
    # **R-FW**).  A row recorded by an adapter that could not read it carries
    # NULL, and without this arm a re-import of the very same file would leave
    # it NULL forever -- the exact defect the running-balance arm above exists
    # for, on a column that feeds a date write instead of a display.  Found by
    # adversarial financial review 2026-08-18, which measured the re-import
    # leaving the column untouched.
    if recorded.transaction_on is None and line.transaction_on is not None:
        recorded.transaction_on = line.transaction_on
    # **The merchant joins the same rule at plan step X-f6a-3d**, and it is the
    # one a RULE matches on: a line whose merchant is NULL joins no destination
    # policy, so a row recorded by an adapter that could not name a merchant
    # would go on being offered a bare chooser forever, even after an export
    # that DOES name one had been imported over it.  The direction is the same
    # as every arm above -- ``NULL`` is filled, a disagreement is left alone --
    # and a disagreement cannot arrive here anyway: this merchant is read from
    # the same cell as ``description``, which the pairing that produced this
    # pair matched on.
    if recorded.merchant is None and line.merchant:
        recorded.merchant = line.merchant


def _fresh_lines(
    lines: "list[StatementLine]",
    already: "dict[tuple[date, Decimal], list[BankStatementLine]]",
) -> "list[KeyedLine]":
    """Return the lines not already recorded, refusing any restatement.

    **The partition is total and it is decided per GROUP**, not per line.  Every
    incoming line either pairs with one the app already holds -- by the wording
    the bank stated, which is the only thing about a line the bank authored --
    or it is new; and a group where BOTH halves have an unaccounted-for member
    is the restatement ruling **R-FL** refuses.  The ordinal takes no part in
    that decision (:func:`~._line.pair_by_statement`); it is minted for the
    fresh lines afterwards (:func:`~._line.fresh_ordinals`).

    Args:
        lines: The file's lines, in the file's own order.
        already: What is recorded over the same span, grouped by day and
            amount.

    Returns:
        The lines to write, with their ordinals, in the file's own order.

    Raises:
        StatementLineConflict: When a recorded line is restated differently.
    """
    fresh: "list[tuple[int, KeyedLine]]" = []
    for key, indexes in group_indexes(lines).items():
        recorded = already.get(key, [])
        pairing = pair_by_statement(
            [lines[index].description for index in indexes],
            [row.description for row in recorded],
        )
        if pairing.restates:
            _refuse_restatement(
                lines[indexes[pairing.fresh[0]]],
                recorded[pairing.unclaimed[0]],
            )
        for incoming_index, recorded_index in pairing.held:
            _absorb_gained_facts(
                lines[indexes[incoming_index]], recorded[recorded_index],
            )
        ordinals = fresh_ordinals(
            (row.sequence_in_group for row in recorded), len(pairing.fresh),
        )
        for ordinal, incoming_index in zip(ordinals, pairing.fresh):
            fresh.append((
                indexes[incoming_index],
                KeyedLine(
                    line=lines[indexes[incoming_index]],
                    sequence_in_group=ordinal,
                ),
            ))
    # Back into the file's own order.  The groups are walked in first-sighting
    # order and their members in file order, so the concatenation is already
    # close -- but "already close" is not an order, and the staged rows' ids
    # are what ``recent_lines`` breaks ties on.
    return [keyed for _, keyed in sorted(fresh, key=lambda pair: pair[0])]


def _stage_lines(
    account_id: int, import_id: int, fresh: "list[KeyedLine]",
) -> None:
    """Stage one :class:`BankStatementLine` per fresh line.

    Args:
        account_id: The account being imported into.
        import_id: The import that is recording them.
        fresh: The lines to write.
    """
    for keyed in fresh:
        line = keyed.line
        db.session.add(BankStatementLine(
            account_id=account_id,
            import_id=import_id,
            posted_on=line.posted_on,
            transaction_on=line.transaction_on,
            amount=line.amount,
            description=line.description,
            merchant=line.merchant,
            source_category=line.source_category,
            external_id=line.external_id,
            sequence_in_group=keyed.sequence_in_group,
            running_balance=line.running_balance,
        ))


def record_statement(
    account_id: int,
    user_id: int,
    source: StatementSourceEnum,
    file_name: str,
    payload: bytes,
) -> ImportOutcome:
    """Record what a statement said, once.

    The whole import, in the order its refusals have to happen: parse, verify
    the file against itself, reconcile the account identity, then compare
    against what is already recorded.  Only after all four does anything get
    staged.

    **Re-importing an overlapping span is a no-op on the lines and still
    records the ACT.**  A line names the import that FIRST recorded it, so the
    original provenance survives, and the second import's ``recorded_count``
    reports honestly that it added nothing.

    Args:
        account_id: The account the user chose.  The CALLER has already proven
            the requesting user owns it -- this door takes an id and does no
            ownership check, exactly as every other service door here does
            (``app/utils/auth_helpers.py`` is the route-side rule).
        user_id: Who performed the import.
        source: Which adapter reads the bytes.
        file_name: The uploaded file's own name, kept as provenance.
        payload: Its raw bytes.

    Returns:
        The :class:`ImportOutcome`.

    Raises:
        StatementParseError: The file is not the shape the adapter reads.
        StatementIntegrityError: The file's running balances do not follow
            from its own lines.
        StatementAccountMismatch: The file is for a different account.
        StatementLineConflict: A recorded line is restated.
    """
    external_account_id, lines = parse_statement(source, payload)
    verify_running_balance(lines)

    source_id = ref_cache.statement_source_id(source)
    identity_is_new = verify_identity(
        account_id, user_id, source_id, external_account_id,
    )

    # MIN/MAX rather than first/last: the span must be total over the file's
    # own days whatever order it arrived in.  The adapter refuses a file that
    # is not in date order, so these agree with the ends today -- and deriving
    # them from the extremes means a future adapter that forgets to sort
    # produces a correct span rather than a backwards one that trips
    # ``ck_statement_imports_period_ordered`` and surfaces as a database error.
    period_start = min(line.posted_on for line in lines)
    period_end = max(line.posted_on for line in lines)
    already = _recorded_groups(account_id, period_start, period_end)

    fresh = _fresh_lines(lines, already)

    # Every refusal is now behind us, so this is the first write.
    if identity_is_new:
        record_identity(account_id, user_id, source_id, external_account_id)

    statement_import = StatementImport(
        account_id=account_id,
        user_id=user_id,
        source_id=source_id,
        file_name=file_name[:255],
        file_digest=hashlib.sha256(payload).hexdigest(),
        period_start=period_start,
        period_end=period_end,
        line_count=len(lines),
        recorded_count=len(fresh),
        opening_balance=opening_balance(lines),
        closing_balance=closing_balance(lines),
    )
    db.session.add(statement_import)
    # The lines carry the import's id in a composite key, so the import row
    # must exist before they are staged.
    db.session.flush()

    _stage_lines(account_id, statement_import.id, fresh)
    db.session.flush()

    return ImportOutcome(
        import_id=statement_import.id,
        line_count=len(lines),
        recorded_count=len(fresh),
        period_start=period_start,
        period_end=period_end,
        opening_balance=statement_import.opening_balance,
        closing_balance=statement_import.closing_balance,
    )
