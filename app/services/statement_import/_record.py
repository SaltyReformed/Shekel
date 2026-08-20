"""The ONE door that records what a statement said.

Everything it can refuse, it refuses BEFORE it writes a row: the file is parsed,
its running-balance chain verified, its account identity reconciled and its
lines compared against what is already recorded, and only then is anything
staged.  So a refused import leaves the database exactly as it was without
depending on the rollback -- and the rollback is there anyway, because the route
owns the commit.

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
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatementSourceEnum
from app.exceptions import StatementAccountMismatch, StatementLineConflict
from app.extensions import db
from app.models.statement_import import (
    AccountExternalIdentity,
    BankStatementLine,
    StatementImport,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_IDENTITY_RECORDED,
    log_event,
)

from ._adapters import parse_statement
from ._integrity import closing_balance, opening_balance, verify_running_balance
from ._line import KeyedLine, assign_sequences

_logger = logging.getLogger(__name__)


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


def _verify_identity(
    account_id: int, user_id: int, source_id: int, external_account_id: str,
) -> bool:
    """Check *account_id*'s identity at *source_id* against the file's.

    Ruling **R-FP**: the source-account mapping is a FACT, not a guess.  The
    user states which account a file is for; the FILE states which account it
    is for; and this is where the two are held together.

    **It reads and raises; it writes nothing.**  Recording is
    :func:`_record_identity`, and the split is not tidiness: the door's whole
    claim is that it refuses BEFORE it stages anything, and an ``add`` here
    would be autoflushed by the very next query -- ahead of the last refusal
    the door can still raise.  A claim that a refusal "leaves the database
    exactly as it was without depending on the rollback" has to be true of the
    ordering, not just of the outcome.

    **It checks in BOTH directions**, and the second one is the arm that
    matters.  Comparing only against this account's own recorded identity would
    let a file already claimed by ANOTHER of the owner's accounts be imported
    here as a first import -- so one bank statement would be recorded twice,
    under two accounts, and the second account's balance would later be
    reconciled against the wrong bank.

    **Both lookups are scoped to the OWNER.**  A global search would make one
    user's masked account number ("******3820", a 10,000-value space) collide
    with another user's, permanently locking the loser out of importing their
    own statements -- and the refusal would disclose that some other account in
    the system held that number, which is the existence oracle the project's
    404-for-both rule exists to prevent.

    Args:
        account_id: The account the user chose.
        user_id: Its owner.
        source_id: The ``ref.statement_sources`` row the file was read by.
        external_account_id: What the file calls its account.

    Returns:
        True when the mapping still has to be RECORDED (a first import), False
        when it already exists and agrees.

    Raises:
        StatementAccountMismatch: When the file names a different account than
            this one has been imported from, or one another of the owner's
            accounts already claims.
    """
    recorded = (
        db.session.query(AccountExternalIdentity)
        .filter(
            AccountExternalIdentity.account_id == account_id,
            AccountExternalIdentity.source_id == source_id,
        )
        .one_or_none()
    )
    if recorded is not None:
        if recorded.external_account_id != external_account_id:
            raise StatementAccountMismatch(
                recorded.external_account_id, external_account_id,
            )
        return False

    claimed_elsewhere = (
        db.session.query(AccountExternalIdentity)
        .filter(
            AccountExternalIdentity.user_id == user_id,
            AccountExternalIdentity.source_id == source_id,
            AccountExternalIdentity.external_account_id
            == external_account_id,
        )
        .one_or_none()
    )
    if claimed_elsewhere is not None:
        raise StatementAccountMismatch(
            "another of your own accounts, which has already imported it",
            external_account_id,
        )
    return True


def _record_identity(
    account_id: int, user_id: int, source_id: int, external_account_id: str,
) -> None:
    """Record what *source_id* calls *account_id*, on a first import.

    Args:
        account_id: The account the user chose.
        user_id: Its owner, held equal to the account's by
            ``fk_account_external_identities_owner``.
        source_id: The adapter the file was read by.
        external_account_id: What the file calls its account.
    """
    db.session.add(AccountExternalIdentity(
        account_id=account_id,
        user_id=user_id,
        source_id=source_id,
        external_account_id=external_account_id,
    ))


def _recorded_lines(
    account_id: int, period_start: date, period_end: date,
) -> "dict[tuple[date, Decimal, int], BankStatementLine]":
    """Return this account's already-recorded lines over the file's span.

    Loaded as ONE query over the day range rather than one per line: a full
    year's export is ~360 lines, and a per-line existence check would be 360
    round trips to answer a question one indexed range scan answers.
    ``idx_bank_statement_lines_account_day`` is the index it uses.

    Args:
        account_id: The account being imported into.
        period_start: The earliest day the file covers.
        period_end: The latest.

    Returns:
        The recorded lines keyed by their account-relative identity.
    """
    rows = (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.posted_on >= period_start,
            BankStatementLine.posted_on <= period_end,
        )
        .all()
    )
    return {
        (row.posted_on, Decimal(str(row.amount)), row.sequence_in_group): row
        for row in rows
    }


def _refuse_restatement(keyed: KeyedLine, recorded: BankStatementLine) -> None:
    """Refuse when a recorded line's own DESCRIPTION is restated.

    A statement line is an OBSERVATION, and an observation quietly rewritten is
    what ruling **R-FL** exists to prevent -- so the fact the source states
    ABOUT THE LINE must agree with what is already recorded.

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

    Args:
        keyed: The incoming line and its ordinal.
        recorded: The line already held at that identity.

    Raises:
        StatementLineConflict: When the descriptions disagree.
    """
    if recorded.description != keyed.line.description:
        raise StatementLineConflict(
            keyed.line.posted_on, keyed.line.amount,
            recorded.description, keyed.line.description,
        )


def _absorb_gained_facts(
    keyed: KeyedLine, recorded: BankStatementLine,
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
        keyed: The incoming line and its ordinal.
        recorded: The line already held at that identity.
    """
    if recorded.running_balance is None and keyed.line.running_balance is not None:
        recorded.running_balance = keyed.line.running_balance
    if recorded.source_category is None and keyed.line.source_category:
        recorded.source_category = keyed.line.source_category
    if recorded.external_id is None and keyed.line.external_id:
        recorded.external_id = keyed.line.external_id
    # **The transaction day joins the same rule at plan step X-f6a-3a**, and
    # it is the one that MOVES A DATE rather than adding provenance: a match
    # writes this day onto a matched purchase's ``purchased_on`` (ruling
    # **R-FW**).  A row recorded by an adapter that could not read it carries
    # NULL, and without this arm a re-import of the very same file would leave
    # it NULL forever -- the exact defect the running-balance arm above exists
    # for, on a column that feeds a date write instead of a display.  Found by
    # adversarial financial review 2026-08-18, which measured the re-import
    # leaving the column untouched.
    if recorded.transaction_on is None and keyed.line.transaction_on is not None:
        recorded.transaction_on = keyed.line.transaction_on
    # **The merchant joins the same rule at plan step X-f6a-3d**, and it is the
    # one a RULE matches on: a line whose merchant is NULL joins no destination
    # policy, so a row recorded by an adapter that could not name a merchant
    # would go on being offered a bare chooser forever, even after an export
    # that DOES name one had been imported over it.  The direction is the same
    # as every arm above -- ``NULL`` is filled, a disagreement is left alone --
    # and a disagreement cannot arrive here anyway: this merchant is read from
    # the same cell as ``description``, which :func:`_refuse_restatement`
    # compares first.
    if recorded.merchant is None and keyed.line.merchant:
        recorded.merchant = keyed.line.merchant


def _fresh_lines(
    keyed_lines: "list[KeyedLine]",
    already: "dict[tuple[date, Decimal, int], BankStatementLine]",
) -> "list[KeyedLine]":
    """Return the lines not already recorded, refusing any restatement.

    The partition is total: every incoming line is either new or is one the app
    already holds, and the second arm is checked rather than assumed
    (:func:`_refuse_restatement`).

    Args:
        keyed_lines: The file's lines with their ordinals.
        already: What is recorded over the same span, by identity.

    Returns:
        The lines to write, in the file's own order.

    Raises:
        StatementLineConflict: When a recorded line is restated differently.
    """
    fresh = []
    for keyed in keyed_lines:
        recorded = already.get(keyed.identity)
        if recorded is None:
            fresh.append(keyed)
        else:
            _refuse_restatement(keyed, recorded)
            _absorb_gained_facts(keyed, recorded)
    return fresh


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
    identity_is_new = _verify_identity(
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
    keyed_lines = assign_sequences(lines)
    already = _recorded_lines(account_id, period_start, period_end)

    fresh = _fresh_lines(keyed_lines, already)

    # Every refusal is now behind us, so this is the first write.
    if identity_is_new:
        _record_identity(account_id, user_id, source_id, external_account_id)
        log_event(
            _logger, logging.INFO, EVT_STATEMENT_IDENTITY_RECORDED, BUSINESS,
            "Recorded which account a statement source calls this account.",
            account_id=account_id, source=source.value,
        )

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
