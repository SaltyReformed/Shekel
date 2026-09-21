"""What comparing a statement against the record DECIDES, before any write.

**Born as a MOVE out of** :mod:`._record` (plan step ``bank_import:X-f6b-2``,
the sync leaf, 2026-09-20, under ruling **balance:R-IR**: *the session that
breaks a module is the one that splits it* -- the door split of ruling
**R-BI30** would otherwise have taken ``_record.py`` past pylint's
1,000-line ceiling, so the half that DECIDES came out first, on its own, and
the half that WRITES stayed).  Every definition below is the one that stood
in ``_record.py`` at the parent tree (``git show
01e218f0:app/services/statement_import/_record.py``): the CODE of each is
byte-for-byte -- ``ast.dump`` with the docstrings set aside equals that of
the node it replaced, graded by name over all seven (the
:mod:`app.models._statement_import_table_args` precedent), and each was
sliced from the flat module by line span, so the comments inside a body moved
with it.  New here: this docstring's frame; the import block; one pronoun in
the paragraph below, which came with its subject and said "this door"; and
the cross-references the move put across a module boundary, which now name
the module (two in the docstrings below, three in ``_record``'s, one in
``_line``'s, and :class:`_Reconciled`'s "the module docstring", which now
says whose).

**Nothing here writes, reads a database, a clock or a request.**  A
:class:`~._line.StatementLine` list and the recorded rows already loaded
over its window come in; the partition -- what is FRESH and what is HELD,
with the ordinals the fresh half is minted -- goes out as a value
(:class:`_Reconciled`), and :mod:`._record` writes both halves after the
last refusal.  The one refusal it raises is the same-source restatement
ruling **R-FL** refuses (:func:`_refuse_restatement`).

**What a file states about a line is that SOURCE's sighting of it** (plan
step ``bank_import:X-f6b-1``, ruling **R-BI10**).  A line is the bank's fact
-- account, day, amount, ordinal -- and every import that shows it writes a
:class:`~app.models.statement_import.StatementLineSighting` carrying its own
wording, id, stated day, balance and category.  So the pairing is within a
SOURCE (:func:`_pair_group`): by the source's own id, then by its wording,
then by count against the lines only other sources have shown; what is left
is new.  A different wording from a different source is never a restatement
(finding **N-303**), and a re-import of a file the app already holds records
one sighting per line and no line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.exceptions import StatementLineConflict
from app.models.statement_import import BankStatementLine

from ._line import (
    KeyedLine,
    StatementLine,
    fresh_ordinals,
    group_indexes,
    pair_by_statement,
)


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
    session -- the caveat :mod:`._record`'s docstring carried.  Both halves
    of the decision are values now, and the writes happen together after
    the last refusal.

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
    (:func:`~._record._refuse_moved_ids`), so the map from id to recorded
    index is a function, and an incoming id it does not hold falls through
    to the wording step.

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
       :func:`~._record._refuse_moved_ids` has already established that
       every held id's line IS in this group.
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
