"""The ONE door that records what a statement said.

Everything it can refuse, it refuses BEFORE it INSERTS a row: the file is
parsed, its running-balance chain verified, its account identity reconciled and
its lines compared against what is already recorded, and only then is anything
staged.  So a refused import writes no new line without depending on the
rollback.

**The claim is about the SESSION too, and it was not until plan step
``bank_import:X-gd-1``.**  The reconciliation walks group by group, and
:func:`_absorb_gained_facts` used to fill a recorded row's NULLs as it went --
so a refusal raised on group *k* left groups 1..*k*-1 dirty in the session and
it was the route's rollback that discarded them.  Nothing was lost by that,
because those writes only ever ADD a fact the file states, but "leaves the
database exactly as it was without depending on the rollback" was true of the
older per-line loop and was not true of that one (found by adversarial
financial review 2026-08-20).  :func:`_reconcile` now DECIDES the whole
partition and returns it; the absorbing happens beside the staging, after the
last refusal, so the session is untouched by a refused import.  The change was
forced -- a merchant is a row now, and a row cannot be resolved for a file that
is about to be refused -- and the older claim is what it restores.

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
        line_count: Lines the file held.
        recorded_count: Lines this import wrote.
        period_start: The earliest day the file covers.
        period_end: The latest.
        balance: What the file claimed the account held and what this import
            made of it, or ``None`` when the file states no balance.  The same
            value the row stores, so the receipt and the page say one thing.
    """

    import_id: int
    line_count: int
    recorded_count: int
    period_start: date
    period_end: date
    balance: ImportedBalance | None

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
    merchants: "dict[str, int]",
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
        merchants: This pass's merchant rows by name
            (:func:`~._merchants.resolve_merchants`), TOTAL over every name a
            line here can carry -- :func:`_merchant_words`' second half is what
            puts them in it.
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
    if recorded.merchant_id is None and line.merchant:
        recorded.merchant_id = merchants[line.merchant]


@dataclass(frozen=True)
class _Reconciled:
    """What comparing a file against the record DECIDED, before any write.

    Plan step ``bank_import:X-gd-1``.  :func:`_reconcile` used to write as it
    decided, calling :func:`_absorb_gained_facts` inside its own loop, so a
    refusal on a later group left earlier ones dirty in the session -- the
    caveat the module docstring carried.  Both halves of the decision are
    values now, and the writes happen together after the last refusal.

    Attributes:
        fresh: The lines to write, with their ordinals, in the file's own
            order.
        absorbing: Every ``(incoming, recorded)`` pair whose recorded row this
            file may fill a NULL on (:func:`_absorb_gained_facts`), in group
            order.  It is a PAIR and not a row because what is absorbed comes
            from the incoming line.
    """

    fresh: "list[KeyedLine]"
    absorbing: "list[tuple[StatementLine, BankStatementLine]]"


def _reconcile(
    lines: "list[StatementLine]",
    already: "dict[tuple[date, Decimal], list[BankStatementLine]]",
) -> _Reconciled:
    """Decide what this file adds and what it fills in, refusing a restatement.

    **The partition is total and it is decided per GROUP**, not per line.  Every
    incoming line either pairs with one the app already holds -- by the wording
    the bank stated, which is the only thing about a line the bank authored --
    or it is new; and a group where BOTH halves have an unaccounted-for member
    is the restatement ruling **R-FL** refuses.  The ordinal takes no part in
    that decision (:func:`~._line.pair_by_statement`); it is minted for the
    fresh lines afterwards (:func:`~._line.fresh_ordinals`).

    **It DECIDES and does not write** (plan step ``bank_import:X-gd-1``).  It
    absorbed as it went until then, which put a write ahead of a refusal this
    same loop can still raise; now the caller writes both halves after the
    file's last refusal, which is what lets a merchant word be resolved to a
    row for a file that is going to be recorded rather than for one that is
    about to be refused.

    Args:
        lines: The file's lines, in the file's own order.
        already: What is recorded over the same span, grouped by day and
            amount.

    Returns:
        The :class:`_Reconciled` decision.

    Raises:
        StatementLineConflict: When a recorded line is restated differently.
    """
    fresh: "list[tuple[int, KeyedLine]]" = []
    absorbing: "list[tuple[StatementLine, BankStatementLine]]" = []
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
            absorbing.append((
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
        absorbing=absorbing,
    )


def _merchant_words(reconciled: _Reconciled) -> "set[str]":
    """Return every merchant word this pass will need a row for.

    **Both halves, and the second is not decoration**: a re-import fills a
    recorded line's merchant where the first adapter could not name one
    (:func:`_absorb_gained_facts`), so a word that appears on NO fresh line can
    still be written.  Asking only about the fresh half would leave that arm
    indexing a mapping the word is not in.

    Args:
        reconciled: What :func:`_reconcile` decided.

    Returns:
        The words, as a set.  A line naming none contributes nothing, which is
        the source saying it names none.
    """
    words = {
        keyed.line.merchant for keyed in reconciled.fresh
        if keyed.line.merchant
    }
    words.update(
        line.merchant for line, recorded in reconciled.absorbing
        if recorded.merchant_id is None and line.merchant
    )
    return words


def _stage_lines(
    account_id: int, import_id: int, fresh: "list[KeyedLine]",
    merchants: "dict[str, int]",
) -> None:
    """Stage one :class:`BankStatementLine` per fresh line.

    Args:
        account_id: The account being imported into.
        import_id: The import that is recording them.
        fresh: The lines to write.
        merchants: This pass's merchant rows by name
            (:func:`~._merchants.resolve_merchants`), TOTAL over every word a
            fresh line names -- :func:`_merchant_words` is what puts them in
            it, so this indexes rather than defaulting.
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
            # ``None`` where the source names none, which keys no rule -- the
            # direction a missing fact has to fail in.
            merchant_id=(
                merchants[line.merchant] if line.merchant else None
            ),
            source_category=line.source_category,
            external_id=line.external_id,
            sequence_in_group=keyed.sequence_in_group,
            running_balance=line.running_balance,
        ))


def _write_records(
    account_id: int, import_id: int, reconciled: _Reconciled,
) -> None:
    """Write everything this file adds to the record, in one merchant pass.

    **The three writes that share one fact, kept together because of it** (plan
    step ``bank_import:X-gd-1``).  A merchant WORD becomes a merchant ROW here
    and nowhere else (:func:`~._merchants.resolve_merchants`); a re-import then
    fills the merchant a recorded row is missing, and every fresh line names
    one.  Splitting them would mean resolving the same words twice or threading
    a mapping across the door, and the two callers are two lines apart.

    **It runs after the last refusal**, which is what the module's opening
    claim rests on: this is the first statement that INSERTS, and a file about
    to be refused leaves nothing behind without depending on the rollback.

    One statement for the whole pass, not one per line: the developer's own
    year-to-date export is 361 lines naming 62 merchants.

    Args:
        account_id: The account being imported into.
        import_id: The import recording the fresh lines.
        reconciled: What :func:`_reconcile` decided this file adds and fills.
    """
    merchants = resolve_merchants(account_id, _merchant_words(reconciled))
    for line, recorded in reconciled.absorbing:
        _absorb_gained_facts(line, recorded, merchants)
    _stage_lines(account_id, import_id, reconciled.fresh, merchants)


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
        StatementBalanceUnexplained: The file states a balance that no day it
            covers reconciles with what is already known.
        StatementAccountMismatch: The file is for a different account.
        StatementLineConflict: A recorded line is restated.
    """
    parsed = parse_statement(source, payload)
    verify_running_balance(parsed.lines)

    source_id = ref_cache.statement_source_id(source)
    identity_is_new = verify_identity(
        account_id, user_id, source_id, parsed.external_account_id,
    )

    # MIN/MAX rather than first/last: the span must be total over the file's
    # own days whatever order it arrived in.  The adapter refuses a file that
    # is not in date order, so these agree with the ends today -- and deriving
    # them from the extremes means a future adapter that forgets to sort
    # produces a correct span rather than a backwards one that trips
    # ``ck_statement_imports_period_ordered`` and surfaces as a database error.
    period_start = min(line.posted_on for line in parsed.lines)
    period_end = max(line.posted_on for line in parsed.lines)

    reconciled = _reconcile(
        parsed.lines,
        _recorded_groups(account_id, period_start, period_end),
    )
    # Read BEFORE the import row exists, so the walk sees only what was
    # recorded before this act -- and resolve the anchor here, with the other
    # refusals, because an unexplained balance must refuse the file rather
    # than be discovered after its lines are staged.
    balance = resolve_anchor(
        parsed.lines,
        parsed.stated_balance,
        parsed.stated_balance_on,
        recorded_opening_before(account_id, period_start),
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
        period_start=period_start,
        period_end=period_end,
        line_count=len(parsed.lines),
        recorded_count=len(reconciled.fresh),
        # The bank's OWN claim, verbatim, beside what this import worked out
        # about it.  The claim and the day it is FOR are two facts (ruling
        # **R-GF**): SECU writes the figure as of the export INSTANT and
        # labels it with the export's day, so on the developer's 2026-08-16
        # file these two columns read 08-16 and the anchor reads 08-13.
        stated_balance=parsed.stated_balance,
        stated_balance_on=parsed.stated_balance_on,
        balance_effective_on=balance.effective_on if balance else None,
        balance_evidence_id=(
            ref_cache.statement_balance_evidence_id(balance.evidence)
            if balance is not None and balance.is_anchored else None
        ),
    )
    db.session.add(statement_import)
    # The lines carry the import's id in a composite key, so the import row
    # must exist before they are staged.
    db.session.flush()

    _write_records(account_id, statement_import.id, reconciled)
    # **Every anchor these fresh lines undercut is RELEASED**, and it happens
    # after the staging so the earliest fresh day is known.  An anchor solved
    # before a line at or before its own day was recorded was solved without
    # that line, and an adversarial review reproduced one storing a day two
    # days early under a *corroborated* badge because of it.  This import's
    # OWN anchor is not among them: it was solved against its own complete
    # line list, so it accounts for every line staged here.
    if reconciled.fresh:
        release_anchors_from(
            account_id,
            min(keyed.line.posted_on for keyed in reconciled.fresh),
            except_import_id=statement_import.id,
        )
    db.session.flush()

    return ImportOutcome(
        import_id=statement_import.id,
        line_count=len(parsed.lines),
        recorded_count=len(reconciled.fresh),
        period_start=period_start,
        period_end=period_end,
        balance=balance,
    )
