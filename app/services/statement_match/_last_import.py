"""What the account's MOST RECENT import did, beside the hero.

Plan step ``bank_import:X-gj-1b``.  The locked direction
(``docs/design/bank_import_audit.md``, "The page") puts one line right of the
hero's four figures -- *Last import <day> - N lines recorded - N filed by
rules - receipt* -- so that a routine session reads *import, read the receipt,
work the inbox, see the difference reach zero*.  This module is the whole of
what that line states.

**It is PROVENANCE and not a figure that answers "am I done"**, which is why
it is its own value rather than three more fields on
:class:`~._reconcile.Hero`.  That class carries one comparison on one day and
documents its three money figures as all-or-nothing; what an import wrote is a
fact about an act the owner performed, and it is present or absent on its own.

**One row read and, when it finds one, one COUNT** -- two questions of two
tables, and the second is not asked at all of an account nobody has imported
into.  How many lines an import WROTE is its own stored column -- the same
``recorded_count`` the statements page's *New* column prints, so the two
surfaces cannot report one import two ways -- and how many of those lines a
STANDING RULE filed is a fact about the acts that name them.  Counting the
lines rather than the acts is what makes the sentence's halves comparable: a
rule files one line per act today (:func:`~._filing.file_new_swipes` submits
one :class:`~._creations.PurchaseCreation` per line), so the two agree on
every input that exists, and counting lines stays true of a multi-line act
that does not exist yet.

**The rule-filed count is EXACT rather than an approximation of one.**
``applied_by_rule`` is written only by a :attr:`~._batch.Consent.STANDING_RULE`
pass, the only door that assembles one is :func:`~._filing.file_new_swipes`,
and it reaches ``_fresh_line_ids(account_id, import_id)`` alone (ruling
**bank_import:R-GI**) -- so a rule-filed act naming a line of import X was
performed by import X's own filing run, and no second column has to record
that.

Measured on a restored production clone 2026-08-30 (``shekel_xgj1b_m``, the
developer's own account 1): three imports, the newest of them
``2026-08-24 20:45 UTC`` recording **2** of the **42** lines its file held --
which is what a re-import of an overlapping span looks like, and why the line
states what was RECORDED rather than what the file contained.

**The two reads narrow by DIFFERENT columns, and that is deliberate rather
than an oversight.**  The ACTS are narrowed by owner and account, which is
what :func:`~._accepted_view.accepted_counts` and
:func:`~._filing.rule_filed_acts` both do and for the reason recorded there.
The IMPORT is narrowed by account ALONE, exactly as
:func:`~app.services.statement_import.import_history` narrows it: an import
belongs to one account, the route has already proved that account is the
caller's, and ``statement_imports.user_id`` records WHO PERFORMED the import
rather than who owns the row.  Adding it here would read as a second
ownership check while actually being a filter that could one day hide a real
import from the account's owner.  Censused on a restored production clone
2026-08-30: 0 imports carry a ``user_id`` differing from their account's.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import, no clock read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.extensions import db
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.statement_match import StatementMatch, StatementMatchMember

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from datetime import datetime


@dataclass(frozen=True)
class LastImport:
    """What the newest import on this account did.

    Attributes:
        at: WHEN the import ran, as the stored UTC instant.  **Not a date**,
            because truncating it here would truncate it in UTC: an import
            performed at 21:00 Eastern is stored on the NEXT UTC day, and the
            owner would read their own evening's work under tomorrow's date.
            The display timezone is the presentation layer's
            (``local_datetime``), which is the project's standing rule for
            every ``timestamptz`` it renders.
        recorded_count: How many lines that import WROTE -- its own
            ``recorded_count`` column, so this line and the statements page's
            *New* column are one fact.  It is not the size of the file: a
            re-import of an overlapping span records 0, which is what makes
            idempotency visible rather than merely true.
        filed_by_rules: How many of those lines a STANDING RULE filed by
            itself (**R-GH**, **R-GT**).  A SUBSET of *recorded_count* by
            construction -- every line it counts carries that import's own
            ``import_id`` -- so the sentence's two halves can be read against
            each other.
    """

    at: "datetime"
    recorded_count: int
    filed_by_rules: int


def _filed_by_rules(owner_id: int, account_id: int, import_id: int) -> int:
    """Return how many of *import_id*'s lines a standing rule filed.

    **Joined on the COMPOSITE keys the model states**, so the account equality
    travels IN the join as well as in this reader's own ``WHERE`` -- the
    reason :class:`~app.models.statement_match.StatementMatchMember`'s own
    relationships are composite (finding **bank_import:N-358**, closed at plan
    step ``bank_import:X-gf-2``).  The filter below bounds the LINES to one
    account; the joins carry that same equality down to the member and the
    act, so no subject reached here can belong to another account whatever a
    future caller passes.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account, which bounds both the lines and the acts.
        import_id: The import whose lines to count over.

    Returns:
        The number of lines of that import which a rule-filed act names.
        ``0`` for an account whose rules have never fired.

        **One row per line, with no ``DISTINCT`` to make it so.**
        ``uq_statement_match_members_line`` is a UNIQUE partial index on
        ``bank_statement_line_id`` alone, so a line belongs to at most one
        match and this join can emit it at most once.  A de-duplication here
        would be a fence over a guarantee the database already holds.
    """
    return (
        db.session.query(db.func.count(BankStatementLine.id))
        .join(
            StatementMatchMember,
            db.and_(
                StatementMatchMember.bank_statement_line_id
                == BankStatementLine.id,
                StatementMatchMember.account_id == BankStatementLine.account_id,
            ),
        )
        .join(
            StatementMatch,
            db.and_(
                StatementMatch.id == StatementMatchMember.match_id,
                StatementMatch.account_id == StatementMatchMember.account_id,
            ),
        )
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.import_id == import_id,
            StatementMatch.user_id == owner_id,
            StatementMatch.applied_by_rule.is_(True),
        )
        .scalar()
    )


def last_import(owner_id: int, account_id: int) -> "LastImport | None":
    """Return what the newest import on *account_id* did, or ``None``.

    Args:
        owner_id: The user the route proved owns the account.  It narrows the
            ACTS, which is the narrowing :func:`~._accepted_view
            .accepted_counts` and :func:`~._filing.rule_filed_acts` both apply
            for the reason recorded there: a reader feeding a screen narrows
            by the same columns the write door does.
        account_id: The account whose imports to read.  It alone narrows the
            IMPORT, exactly as :func:`~app.services.statement_import
            .import_history` does -- an import belongs to one account, and the
            route has already proved that account is the caller's.

    Returns:
        The :class:`LastImport`, or ``None`` for an account nobody has
        imported into -- which is a real and ordinary state, and the state the
        hero already has its own arm for.
    """
    # **Ordered by the instant AND then by id**, which is
    # :func:`~app.services.statement_import.import_history`'s own key and is
    # load-bearing rather than decorative: ``created_at`` defaults to
    # ``now()``, which in PostgreSQL is the TRANSACTION's start time, so two
    # imports written in one transaction carry the identical instant and the
    # id is the only thing that orders them.
    #
    # **It is the SECOND spelling of that key and it cannot be hoisted**:
    # ``statement_import`` imports THIS package (``_reads`` takes
    # ``removals_by_match``, ``_undo`` takes ``release_match``), so an edge
    # back would close a cycle -- the same wall :func:`~._filing
    # .file_new_swipes` documents for the ``ImportOutcome`` it cannot take.
    # No model in this app carries a query-ordering constant, so inventing a
    # third home for one ``ORDER BY`` would be the speculative abstraction
    # ``CLAUDE.md`` rule 13 forbids.  What keeps the two honest is that both
    # say why the id is there.
    found = (
        db.session.query(
            StatementImport.id,
            StatementImport.created_at,
            StatementImport.recorded_count,
        )
        .filter(StatementImport.account_id == account_id)
        .order_by(StatementImport.created_at.desc(), StatementImport.id.desc())
        .first()
    )
    if found is None:
        return None
    import_id, created_at, recorded_count = found
    return LastImport(
        at=created_at,
        recorded_count=recorded_count,
        filed_by_rules=_filed_by_rules(owner_id, account_id, import_id),
    )
