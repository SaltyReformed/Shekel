"""The ONE door that records what a statement said.

Everything it can refuse, it refuses BEFORE it INSERTS a row: the file is
parsed, its running-balance chain verified, its account identity reconciled and
its lines compared against what is already recorded, and only then is anything
staged.  So a refused import writes no new line without depending on the
rollback.

**The claim is about the SESSION too, and it was not until plan step
``bank_import:X-gd-1``.**  The reconciliation walks group by group, and the
absorbing arm this door had then filled a recorded row's NULLs as it went --
so a refusal raised on group *k* left groups 1..*k*-1 dirty in the session and
it was the route's rollback that discarded them.  Nothing was lost by that,
because those writes only ever ADD a fact the file states, but "leaves the
database exactly as it was without depending on the rollback" was true of the
older per-line loop and was not true of that one (found by adversarial
financial review 2026-08-20).  :func:`_reconcile` now DECIDES the whole
partition and returns it; the writes happen beside the staging, after the
last refusal, so the session is untouched by a refused import.  The change was
forced -- a merchant is a row now, and a row cannot be resolved for a file that
is about to be refused -- and the older claim is what it restores.

**What a file states about a line is that SOURCE's sighting of it** (plan
step ``bank_import:X-f6b-1``, ruling **R-BI10**).  A line is the bank's fact
-- account, day, amount, ordinal -- and every import that shows it writes a
:class:`~app.models.statement_import.StatementLineSighting` carrying its own
wording, id, stated day, balance and category.  So this door pairs within a
SOURCE (:func:`_pair_group`): by the source's own id, then by its wording,
then by count against the lines only other sources have shown; what is left
is new.  A different wording from a different source is never a restatement
(finding **N-303**), and a re-import of a file the app already holds records
one sighting per line and no line.

**A re-import writes NOTHING onto a recorded line** (plan step
``bank_import:X-f6b-1b``, ruling **R-BI16**).  Every fact a source states
about a line is its sighting's, the merchant KEY included: the sighting
carries the merchant its word names, and the line's merchant is a read over
its sightings.  Until that step this door filled a held line's NULL key from
a later sighting -- an ``UPDATE`` on the line, and a key that outlived the
import that minted it (finding **BI-504**).  Now a re-import UPDATEs no
bank line: what it writes FOR a held line is a sighting, whose insert takes
``FOR KEY SHARE`` on the line and nothing stronger (the import row, the
merchant rows and, when the file states one, the level are the pass's other
inserts, none of them on a line).

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
from app.exceptions import (
    StatementLineConflict,
    StatementLineIdMoved,
    StatementParseError,
)
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)

from ._adapters import parse_statement
from ._anchor import (
    ImportedBalance,
    recorded_opening_before,
    release_anchors_from,
    resolve_anchor,
)
from ._identity import record_identity, verify_identity
from ._integrity import verify_running_balance
from ._line import (
    KeyedLine,
    StatementLine,
    fresh_ordinals,
    group_indexes,
    group_key,
    pair_by_statement,
)
from ._merchants import resolve_merchants


@dataclass(frozen=True)
class ImportOutcome:
    """What one import act did.

    Attributes:
        import_id: The ``budget.statement_imports`` row recording the act.
        line_count: Lines the file held -- each now a sighting of this
            import's.  **Read from the ONE aggregate the imports table and
            the hero read** (:meth:`~app.models.statement_import
            .StatementLineSighting.counts_by_import`) after the sightings
            are flushed, and stored nowhere (plan step
            ``bank_import:X-f6b-1``, ruling **R-IY**); a receipt that
            counted the parsed lines itself would be a second spelling of
            the same figure, agreeing today and free to part.
        recorded_count: Lines this import was the FIRST to sight, from the
            same aggregate.
        declared_start: The first day the import DECLARED it answers for
            (``StatementImport.declared_start``).
        declared_end: The last.
        balance: What the file claimed the account held and what this import
            made of it, or ``None`` when the file states no balance.  The same
            value the row stores, so the receipt and the page say one thing.
        anchors_released: Other imports whose placed balance rested on days
            this import's fresh lines reach, and which
            :func:`~._anchor.release_anchors_from` therefore released.  **On
            the receipt since plan step ``bank_import:X-gr``** (finding
            **BI-488**): the door counted them and dropped the count, so an
            owner learned that an import had taken a checked balance away
            from an earlier one only from the imports table's badge flipping
            to *not placed*, and only if they looked.  Zero for an import
            that recorded no fresh line, because a line already recorded
            undercuts nothing.
    """

    import_id: int
    line_count: int
    recorded_count: int
    declared_start: date
    declared_end: date
    balance: ImportedBalance | None
    anchors_released: int

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
    account_id: int, declared_start: date, declared_end: date,
) -> "dict[tuple[date, Decimal], list[BankStatementLine]]":
    """Return this account's already-recorded lines over the declared window.

    Loaded as ONE query over the day range rather than one per line: a full
    year's export is ~360 lines, and a per-line existence check would be 360
    round trips to answer a question one indexed range scan answers.
    ``idx_bank_statement_lines_account_day`` is the index it uses; the
    sightings each line carries, and the import behind each sighting, ride in
    the same statement (both relationships are ``joined``), because the
    reconciliation reads every one of them.

    **Grouped by ``(posted_on, amount)`` rather than keyed by the full
    identity**, which is what makes the reconciliation set-wise: the recorded
    ordinal is a surrogate this app assigned, so a group is looked up by what
    the BANK stated and the members inside it are then paired on what THIS
    SOURCE stated (:func:`_pair_group`).

    Args:
        account_id: The account being imported into.
        declared_start: The first day the file declares it answers for.
        declared_end: The last.  Every line the file states falls inside the
            window, so every line it could pair with is loaded.

    Returns:
        The recorded lines grouped by the day and amount they share, each
        group ordered by its own ordinal so the count pairing walks a stable
        sequence.
    """
    rows = (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.posted_on >= declared_start,
            BankStatementLine.posted_on <= declared_end,
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


def _held_ids(
    account_id: int, source_id: int, lines: "list[StatementLine]",
) -> "dict[str, tuple[date, Decimal]]":
    """Return where THIS SOURCE already holds each id the file states.

    ONE query over the whole account rather than over the declared window,
    because the shape it exists to refuse is an id held on a line OUTSIDE the
    window: a source restating a line's day or amount under the same id
    (:func:`_refuse_moved_ids`).  Empty for a file carrying no ids, which
    issues no statement -- SECU's CSV carries none.

    Args:
        account_id: The account being imported into.
        source_id: The import's source; another source's ids are its own.
        lines: The file's lines.

    Returns:
        ``{external_id: (posted_on, amount)}`` for every stated id one of
        this source's sightings carries, the line's group key beside it.
    """
    stated = {line.external_id for line in lines if line.external_id}
    if not stated:
        return {}
    rows = (
        db.session.query(
            StatementLineSighting.external_id,
            BankStatementLine.posted_on, BankStatementLine.amount,
        )
        .join(BankStatementLine, StatementLineSighting.of_its_line())
        .join(StatementImport, StatementLineSighting.of_its_import())
        .filter(
            StatementLineSighting.account_id == account_id,
            StatementImport.source_id == source_id,
            StatementLineSighting.external_id.in_(stated),
        )
        .all()
    )
    return {
        external_id: group_key(posted_on, amount)
        for external_id, posted_on, amount in rows
    }


def _refuse_repeated_ids(lines: "list[StatementLine]") -> None:
    """Refuse a file that states one id on two of its own lines.

    A source names ONE line per id, and every rule downstream rests on it:
    the id step of :func:`_pair_group` maps an id to one recorded line, and
    :func:`_held_ids` reads one group per id.  A file carrying an id twice
    would record two lines under it -- the first pairing to the held line and
    the second minted fresh beside it -- and the next import's lookup would
    hold that id on whichever row came back last.  Found by adversarial
    review 2026-09-18; the unique index the sighting relation retired had
    refused this as a database error.

    Args:
        lines: The file's lines.

    Raises:
        StatementParseError: On the first id the file states twice.  A parse
            refusal, because a source that repeats an id has been read
            wrongly or is defective, and nothing about the account can
            repair that.
    """
    seen: "set[str]" = set()
    for line in lines:
        if not line.external_id:
            continue
        if line.external_id in seen:
            raise StatementParseError(
                f"This file states line id '{line.external_id}' on two "
                f"lines.  A source names one line per id, so the file was "
                f"read wrongly or is defective.  Nothing was imported."
            )
        seen.add(line.external_id)


def _refuse_moved_ids(
    lines: "list[StatementLine]", held: "dict[str, tuple[date, Decimal]]",
) -> None:
    """Refuse a file that states a held id on another day or amount.

    A source names ONE line per id, and the pairing that honours that
    (:func:`_pair_group`, step 1) works within a ``(day, amount)`` group -- so
    an id whose recorded line is in a DIFFERENT group is the one shape the
    pairing cannot reach and recording would make two lines of.  While the id
    lived on the line, ``uq_bank_statement_lines_external_id`` refused that
    state as a database error; this is the same refusal with a sentence.

    Args:
        lines: The file's lines.
        held: :func:`_held_ids`' answer.

    Raises:
        StatementLineIdMoved: On the first stated id whose recorded group is
            not the group this file states it in.
    """
    for line in lines:
        if not line.external_id:
            continue
        recorded = held.get(line.external_id)
        stated = group_key(line.posted_on, line.amount)
        if recorded is not None and recorded != stated:
            raise StatementLineIdMoved(line.external_id, recorded, stated)


def _sightings_from(row: BankStatementLine, source_id: int) -> list:
    """Return *row*'s sightings by THIS source, latest first.

    Latest by :attr:`~app.models.statement_import.StatementImport.act_key`,
    so the wording a same-source re-import is compared against is what this
    source said most recently.

    Args:
        row: A recorded line, its sightings loaded.
        source_id: The source to select.

    Returns:
        The :class:`~app.models.statement_import.StatementLineSighting` rows,
        possibly empty -- another source showed this line.
    """
    return sorted(
        (
            sighting for sighting in row.sightings
            if sighting.statement_import.source_id == source_id
        ),
        key=lambda sighting: sighting.statement_import.act_key,
        reverse=True,
    )


def _refuse_restatement(line: StatementLine, recorded: str) -> None:
    """Refuse when a source restates a line's own DESCRIPTION.

    A statement line is an OBSERVATION, and an observation quietly rewritten is
    what ruling **R-FL** exists to prevent -- so the fact a source states
    ABOUT A LINE must agree with what that same source already stated.

    **What reaches this is a group where THIS SOURCE's own sightings hold a
    member the file no longer states beside an incoming member nothing
    accounts for** (:func:`_pair_group`), which is the only shape that is a
    contradiction rather than a change in what the export covers.  A wording
    another source used is never compared -- two sources call one line two
    things, and reading that as a restatement is finding **N-303**, the
    defect the sighting relation exists to close.  It used to be reached by a
    positional compare, and that fired on two events the bank had not restated
    at all: a re-ordered pair of same-day same-amount lines, and a genuinely
    new line the bank inserted ahead of a recorded one.

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
    once recorded it is a sighting's provenance.

    **The two wordings it names are EXAMPLES, not a pairing.**
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
        recorded: What this source called one recorded line in the same group
            that the file no longer states.

    Raises:
        StatementLineConflict: Always.  The caller has already established that
            this group restates something, so a guard here would be a second
            copy of that decision.
    """
    raise StatementLineConflict(
        line.posted_on, line.amount, recorded, line.description,
    )


@dataclass(frozen=True)
class _Reconciled:
    """What comparing a file against the record DECIDED, before any write.

    Plan step ``bank_import:X-gd-1``.  :func:`_reconcile` used to write as it
    decided, so a refusal on a later group left earlier ones dirty in the
    session -- the caveat the module docstring carried.  Both halves of the
    decision are values now, and the writes happen together after the last
    refusal.

    Attributes:
        fresh: The lines to write, with their ordinals, in the file's own
            order.  Each becomes a line AND this import's sighting of it.
        held: Every ``(incoming, recorded)`` pair the file states a line the
            app already holds by, in group order.  Each becomes this import's
            sighting of the recorded line, and nothing on the line.  It is a
            PAIR and not a row because what the sighting records comes from
            the incoming line.
    """

    fresh: "list[KeyedLine]"
    held: "list[tuple[StatementLine, BankStatementLine]]"


@dataclass(frozen=True)
class _GroupPairing:
    """How one group's incoming lines pair against its recorded ones.

    Attributes:
        held: ``(incoming index, recorded index)`` pairs.
        fresh: The incoming indexes nothing recorded accounts for.
        restated: What THIS source called a recorded member the file no
            longer states, or ``None``.  Set only when :attr:`fresh` is also
            non-empty, which is the contradiction :func:`_refuse_restatement`
            refuses; a same-source member the file merely omits is a shorter
            export and refuses nothing.
    """

    held: "list[tuple[int, int]]"
    fresh: "list[int]"
    restated: "str | None"


def _pair_by_id(
    incoming: "list[StatementLine]", by_source: "list[list]", same: "list[int]",
) -> "tuple[list[tuple[int, int]], list[int]]":
    """Pair incoming lines carrying an id to the lines this source holds them on.

    Step 1 of :func:`_pair_group`.  A source holds one line per id
    (:func:`_refuse_moved_ids`), so the map from id to recorded index is a
    function, and an incoming id it does not hold falls through to the
    wording step.

    Args:
        incoming: The group's incoming lines, in file order.
        by_source: Per recorded line, its sightings by this source
            (:func:`_sightings_from`).
        same: The recorded indexes with at least one such sighting.

    Returns:
        ``(held, unpaired)`` -- the ``(incoming, recorded)`` index pairs, and
        the incoming indexes left for the steps after.
    """
    id_of = {
        sighting.external_id: index
        for index in same
        for sighting in by_source[index]
        if sighting.external_id
    }
    held: "list[tuple[int, int]]" = []
    claimed: "set[int]" = set()
    unpaired: "list[int]" = []
    for index, line in enumerate(incoming):
        target = id_of.get(line.external_id) if line.external_id else None
        if target is not None and target not in claimed:
            held.append((index, target))
            claimed.add(target)
        else:
            unpaired.append(index)
    return held, unpaired


def _pair_group(
    incoming: "list[StatementLine]", recorded: "list[BankStatementLine]",
    source_id: int,
) -> _GroupPairing:
    """Pair one ``(day, amount)`` group's incoming lines to its recorded ones.

    **The pairing rule of ruling R-BI10, in its four steps**, each consuming
    what the one before it left:

    1. An incoming line carrying an id pairs to the recorded line a sighting
       of THIS SOURCE already carries that id on (:func:`_pair_by_id`).  The
       id breaks ties within the group; it is not the identity, and
       :func:`_refuse_moved_ids` has already established that every held
       id's line IS in this group.
    2. The rest pair by WORDING against the recorded lines this source has
       sighted (:func:`~._line.pair_by_statement`, multiset, unchanged) --
       against what this source called each of them most recently.
    3. The rest pair by COUNT, in ordinal order, against the recorded lines
       NO sighting of this source holds: another source showed them, its
       wording is expected to differ, and a different wording across sources
       is never a restatement (finding **N-303**).
    4. The rest are FRESH.

    **The refusal keeps its reach**: after the three pairings, a group where
    this source's OWN sightings still hold a member the file does not state,
    beside an incoming member nothing accounts for, is the same-source
    restatement ruling **R-FL** refuses.  A member another source holds and
    this file does not state is that source's business.

    Args:
        incoming: The file's lines in this group, in file order.
        recorded: The recorded lines in this group, in ordinal order.
        source_id: The import's source.

    Returns:
        The :class:`_GroupPairing`.
    """
    by_source = [_sightings_from(row, source_id) for row in recorded]
    same = [index for index, seen in enumerate(by_source) if seen]
    held, unpaired = _pair_by_id(incoming, by_source, same)
    # 2. By this source's wording, among what this source has sighted and the
    # id step did not claim.
    claimed = {recorded_index for _, recorded_index in held}
    same_open = [index for index in same if index not in claimed]
    pairing = pair_by_statement(
        [incoming[index].description for index in unpaired],
        [by_source[index][0].description for index in same_open],
    )
    held.extend(
        (unpaired[incoming_offset], same_open[recorded_offset])
        for incoming_offset, recorded_offset in pairing.held
    )
    rest = [unpaired[offset] for offset in pairing.fresh]
    # 3. By count, in ordinal order, against what only other sources showed.
    # ``recorded`` arrives in ordinal order, so index order is ordinal order,
    # and neither earlier step can have claimed one of these.
    other = [index for index in range(len(recorded)) if index not in same]
    held.extend(zip(rest, other))
    fresh = rest[len(other):]
    unclaimed_same = [same_open[offset] for offset in pairing.unclaimed]
    return _GroupPairing(
        held=held,
        fresh=fresh,
        restated=(
            by_source[unclaimed_same[0]][0].description
            if fresh and unclaimed_same else None
        ),
    )


def _reconcile(
    lines: "list[StatementLine]",
    already: "dict[tuple[date, Decimal], list[BankStatementLine]]",
    source_id: int,
) -> _Reconciled:
    """Decide what this file adds and what it re-sights, refusing a restatement.

    **The partition is total and it is decided per GROUP**, not per line.  Every
    incoming line either pairs with one the app already holds
    (:func:`_pair_group`) or it is new; and a group where this source's own
    record and the file each hold a member the other cannot account for is
    the restatement ruling **R-FL** refuses.  The ordinal takes no part in
    that decision; it is minted for the fresh lines afterwards
    (:func:`~._line.fresh_ordinals`).

    **It DECIDES and does not write** (plan step ``bank_import:X-gd-1``).  It
    absorbed as it went until then, which put a write ahead of a refusal this
    same loop can still raise; now the caller writes both halves after the
    file's last refusal, which is what lets a merchant word be resolved to a
    row for a file that is going to be recorded rather than for one that is
    about to be refused.

    Args:
        lines: The file's lines, in the file's own order.
        already: What is recorded over the same window, grouped by day and
            amount.
        source_id: The import's source, which is what a sighting is "from".

    Returns:
        The :class:`_Reconciled` decision.

    Raises:
        StatementLineConflict: When this source restates a line it recorded.
    """
    fresh: "list[tuple[int, KeyedLine]]" = []
    held: "list[tuple[StatementLine, BankStatementLine]]" = []
    for key, indexes in group_indexes(lines).items():
        recorded = already.get(key, [])
        pairing = _pair_group(
            [lines[index] for index in indexes], recorded, source_id,
        )
        if pairing.restated is not None:
            _refuse_restatement(
                lines[indexes[pairing.fresh[0]]], pairing.restated,
            )
        for incoming_index, recorded_index in pairing.held:
            held.append((
                lines[indexes[incoming_index]], recorded[recorded_index],
            ))
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
    return _Reconciled(
        fresh=[keyed for _, keyed in sorted(fresh, key=lambda pair: pair[0])],
        held=held,
    )


def _merchant_words(reconciled: _Reconciled) -> "set[str]":
    """Return every merchant word this pass will need a row for.

    **Both halves, TOTAL**: every sighting this pass writes carries the
    merchant its word names (ruling **R-BI16**), whether the line is fresh or
    held, so every word the file states needs a row.  *Until plan step
    ``bank_import:X-f6b-1b`` the held half asked only for the words filling a
    NULL key on the line*; a held line's word that the line already had a
    key for was never resolved, so a second source's different word for a
    known line had no row at all.

    Args:
        reconciled: What :func:`_reconcile` decided.

    Returns:
        The words, as a set.  A line naming none contributes nothing, which is
        the source saying it names none.
    """
    stated = [keyed.line for keyed in reconciled.fresh]
    stated.extend(line for line, _recorded in reconciled.held)
    return {line.merchant for line in stated if line.merchant}


def _sighting_of(
    account_id: int, import_id: int, line_id: int, line: StatementLine,
    merchants: "dict[str, int]",
) -> StatementLineSighting:
    """Return this import's sighting of one line, from what the source stated.

    THE one place a :class:`~._line.StatementLine`'s per-source facts become a
    row, so the fresh half and the held half of a pass cannot record them
    differently -- the merchant KEY among them (ruling **R-BI16**): the word
    the source named becomes this sighting's ``merchant_id`` here, for a
    fresh line and a held one alike.

    Args:
        account_id: The account being imported into.
        import_id: The import that is recording the sighting.
        line_id: The recorded line it is a sighting of.
        line: What the source stated.
        merchants: This pass's merchant rows by name
            (:func:`~._merchants.resolve_merchants`), TOTAL over every word
            the file states -- :func:`_merchant_words` is what puts them in
            it, so this indexes rather than defaulting.

    Returns:
        The unstaged row.
    """
    return StatementLineSighting(
        account_id=account_id,
        import_id=import_id,
        line_id=line_id,
        description=line.description,
        # ``None`` where the source names none, which keys no rule -- the
        # direction a missing fact has to fail in.
        merchant_id=merchants[line.merchant] if line.merchant else None,
        transaction_on=line.transaction_on,
        external_id=line.external_id,
        running_balance=line.running_balance,
        source_category=line.source_category,
    )


def _stage_lines(
    account_id: int, import_id: int, fresh: "list[KeyedLine]",
    merchants: "dict[str, int]",
) -> None:
    """Stage one :class:`BankStatementLine` and its sighting per fresh line.

    The line needs its id before the sighting can name it, so the lines are
    flushed once, together, between the two loops -- one round trip for the
    pass rather than one per line.  The line takes what every source agrees
    on and nothing else; the merchant the file names goes onto the sighting
    (ruling **R-BI16**).

    Args:
        account_id: The account being imported into.
        import_id: The import that is recording them.
        fresh: The lines to write.
        merchants: This pass's merchant rows by name, for the sightings
            (:func:`_sighting_of`).
    """
    rows = [
        BankStatementLine(
            account_id=account_id,
            posted_on=keyed.line.posted_on,
            amount=keyed.line.amount,
            sequence_in_group=keyed.sequence_in_group,
        )
        for keyed in fresh
    ]
    db.session.add_all(rows)
    db.session.flush()
    db.session.add_all(
        _sighting_of(account_id, import_id, row.id, keyed.line, merchants)
        for row, keyed in zip(rows, fresh)
    )


def _write_records(
    account_id: int, import_id: int, reconciled: _Reconciled,
) -> None:
    """Write everything this file adds to the record, in one merchant pass.

    **The writes that share one fact, kept together because of it** (plan step
    ``bank_import:X-gd-1``).  A merchant WORD becomes a merchant ROW here and
    nowhere else (:func:`~._merchants.resolve_merchants`), and every sighting
    this pass writes -- of a held line or a fresh one -- names its row
    (ruling **R-BI16**).  Splitting them would mean resolving the same words
    twice or threading a mapping across the door, and the callers are a few
    lines apart.

    **Every line the file states gains this import's sighting** (ruling
    **R-BI10**): the held half records what this source said about a line
    the app already holds, the fresh half records the line and what was said.

    **It runs after the last refusal**, which is what the module's opening
    claim rests on: this is the first statement that INSERTS, and a file about
    to be refused leaves nothing behind without depending on the rollback.

    One statement for the whole pass, not one per line: the developer's own
    year-to-date export is 361 lines naming 62 merchants.

    Args:
        account_id: The account being imported into.
        import_id: The import recording the pass.
        reconciled: What :func:`_reconcile` decided this file adds and holds.
    """
    merchants = resolve_merchants(account_id, _merchant_words(reconciled))
    db.session.add_all(
        _sighting_of(account_id, import_id, recorded.id, line, merchants)
        for line, recorded in reconciled.held
    )
    _stage_lines(account_id, import_id, reconciled.fresh, merchants)
    # The held lines' eager ``sightings`` collections and their two merchant
    # projections were loaded BEFORE the rows above existed, and a sighting
    # staged by its ids joins neither a loaded collection nor a loaded
    # subquery answer.  Expire exactly that set, so a reader of a held line's
    # wording, day or merchant in this same unit of work reads the row and
    # not the instance as it was.  ONE spelling of the set
    # (``BankStatementLine.READS_OVER_SIGHTINGS``), shared with the test
    # builder that stages a sighting the same way.
    for _line, recorded in reconciled.held:
        db.session.expire(recorded, BankStatementLine.READS_OVER_SIGHTINGS)


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

    **Re-importing an overlapping span records a SIGHTING of every line it
    already holds and records the ACT** (ruling **R-BI10**).  The line stays
    one line; this import's window now also vouches for its days; and the
    receipt's ``recorded_count`` reports honestly that it added nothing.

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
        StatementParseError: The file is not the shape the adapter reads, or
            states one of its own ids twice.
        StatementIntegrityError: The file's running balances do not follow
            from its own lines.
        StatementBalanceUnexplained: The file states a balance that no day it
            covers reconciles with what is already known.
        StatementAccountMismatch: The file is for a different account.
        StatementLineIdMoved: This source restates a line's day or amount
            under an id it already holds.
        StatementLineConflict: This source restates a line's wording.
    """
    parsed = parse_statement(source, payload)
    verify_running_balance(parsed.lines)

    source_id = ref_cache.statement_source_id(source)
    identity_is_new = verify_identity(
        account_id, user_id, source_id, parsed.external_account_id,
    )

    _refuse_repeated_ids(parsed.lines)
    _refuse_moved_ids(
        parsed.lines, _held_ids(account_id, source_id, parsed.lines),
    )
    reconciled = _reconcile(
        parsed.lines,
        _recorded_groups(
            account_id, parsed.declared_start, parsed.declared_end,
        ),
        source_id,
    )
    # Read BEFORE the import row exists, so the walk sees only what was
    # recorded before this act -- and resolve the anchor here, with the other
    # refusals, because an unexplained balance must refuse the file rather
    # than be discovered after its lines are staged.
    balance = resolve_anchor(
        parsed,
        recorded_opening_before(account_id, parsed.declared_start),
    )

    # Every refusal is now behind us, so this is the first write.
    if identity_is_new:
        record_identity(
            account_id, user_id, source_id, parsed.external_account_id,
        )

    statement_import = StatementImport(
        account_id=account_id,
        user_id=user_id,
        source_id=source_id,
        file_name=file_name[:255],
        file_digest=hashlib.sha256(payload).hexdigest(),
        declared_start=parsed.declared_start,
        declared_end=parsed.declared_end,
        # The bank's OWN claim, verbatim.  The claim and the day it is FOR
        # are two facts (ruling **R-GF**): SECU writes the figure as of the
        # export INSTANT and labels it with the export's day, so on the
        # developer's 2026-08-16 file these two columns read 08-16 and the
        # level below reads 08-13.
        stated_balance=parsed.stated_balance,
        stated_balance_on=parsed.stated_balance_on,
    )
    db.session.add(statement_import)
    # The sightings carry the import's id in a composite key, so the import
    # row must exist before they are staged.
    db.session.flush()

    _write_records(account_id, statement_import.id, reconciled)
    # **What the import made of the claim is a LEVEL** (plan step
    # ``balance:X-bj-1``, ruling **R-IS**): the day the figure is the balance
    # for and how firmly it is held, as a row in the same relation the
    # owner's true-ups occupy, naming this import.  Its amount is the claim
    # -- the key ``fk_anchor_history_statement_import_claim`` would refuse
    # anything else -- and its evidence is what the solve above worked out.
    # Written after the lines because it is a conclusion drawn from them,
    # and inside the declared window by ``budget.level_lies_within_file``.
    if balance is not None and balance.is_anchored:
        db.session.add(AccountAnchorHistory(
            account_id=account_id,
            anchor_balance=balance.stated,
            observed_on=balance.effective_on,
            evidence_id=ref_cache.statement_balance_evidence_id(
                balance.evidence,
            ),
            statement_import_id=statement_import.id,
        ))
    # **Every anchor these fresh lines undercut is RELEASED**, and it happens
    # after the staging so the earliest fresh day is known.  An anchor solved
    # before a line at or before its own day was recorded was solved without
    # that line, and an adversarial review reproduced one storing a day two
    # days early under a *corroborated* badge because of it.  This import's
    # OWN anchor is not among them: it was solved against its own complete
    # line list, so it accounts for every line staged here.  **The count is
    # KEPT** (plan step ``bank_import:X-gr``, finding **BI-488**): it is what
    # tells the receipt that this file took a checked balance away from an
    # earlier import, which until then the owner learned only from the imports
    # table's badge.
    anchors_released = (
        release_anchors_from(
            account_id,
            min(keyed.line.posted_on for keyed in reconciled.fresh),
            statement_import.id,
        )
        if reconciled.fresh else 0
    )
    db.session.flush()

    # The receipt's two counts come from the aggregate every other surface
    # reads, so the receipt, the imports table and the hero cannot part.  A
    # file with no line wrote no sighting and reads ``(0, 0)``.
    counted = db.session.execute(
        StatementLineSighting.counts_by_import(account_id)
        .where(StatementLineSighting.import_id == statement_import.id),
    ).one_or_none()
    return ImportOutcome(
        import_id=statement_import.id,
        line_count=0 if counted is None else counted.sighted,
        recorded_count=0 if counted is None else counted.first,
        declared_start=parsed.declared_start,
        declared_end=parsed.declared_end,
        balance=balance,
        anchors_released=anchors_released,
    )
