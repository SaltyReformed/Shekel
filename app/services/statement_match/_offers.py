"""What a match IS, as values -- the shapes every other module here passes.

Ruling **R-FS** gives a match three shapes and this module gives them one
type.  A :class:`MatchProposal` is a candidate the app OFFERS; a
:class:`MatchSubmission` is what the owner sent back.  They are deliberately
different types rather than one reused both ways: a proposal carries what the
screen needs to explain itself, and a submission carries only ids, so nothing
a user posts can smuggle a figure the app computed.

Services-boundary discipline (``CLAUDE.md`` Architecture): frozen dataclasses,
no Flask import, no query, no clock read.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class RowKind(enum.Enum):
    """Which of the app's two matchable row types a candidate is.

    The app holds a cash movement as one of exactly two things and they settle
    through different verbs: a :class:`~app.models.transaction.Transaction`
    (a bill, a deposit, an envelope's close, a transfer's shadow leg) and a
    :class:`~app.models.transaction_entry.TransactionEntry` (a purchase, which
    is a cash movement of its own since plan step ``balance:X-f3b``).

    **It is TAGGED by the reader that produced the candidate, never derived
    downstream**, which is the rule :class:`OfferKind` states one package over
    after deriving it mis-captioned a live `$1,958.87` reimbursement.
    """

    TRANSACTION = "transaction"
    PURCHASE = "purchase"


@dataclass(frozen=True)
class CandidateRow:  # pylint: disable=too-many-instance-attributes
    """One app row a bank line could be, priced and dated as the app holds it.

    Pylint: too-many-instance-attributes -- **nine fields because the subject
    genuinely has nine**, not because the value wants splitting.
    It describes ONE row drawn from either of two tables, for five consumers
    that each read a different subset: the proposer reads the amount and the
    days, the assignment reads the days, the screen reads the label and the
    kind, the accept door reads the kind, the id and the routing links, and the
    unmatched-rows panel reads the projection day.  ``TransferSpec`` carries
    the same disable for the same reason.  Two fields were MERGED rather than
    disabled around: ``earliest_day`` and ``expected_on`` were one fact for the
    only kind that has both.

    Attributes:
        kind: Which table it came from.
        row_id: Its primary key within that table.
        label: What to call it on screen.
        cash_amount: Its SIGNED cash effect on this account -- positive INTO,
            the same convention ``bank_statement_lines.amount`` uses, so the
            comparison is a subtraction rather than a sign negotiation.
        settled_on: The day the app currently records the money as having
            moved, or ``None`` for a row that has not been settled at all.
        is_settled: Whether the row is already in the settled band.  A match
            SETTLES the first and CORRECTS the second, and which of the two a
            proposal would do is the one thing a reviewer most needs told.
        transfer_id: The parent transfer when this row is a shadow leg, else
            ``None``.  Carried because a shadow settles through
            ``transfer_service`` and not through the transaction verb, and a
            writer that had to re-derive that would be a second place for the
            partition to be stated.
        parent_id: The envelope a PURCHASE belongs to, else ``None`` -- a
            transaction IS a parent and names no other.  Carried so the
            proposer can decline to offer a group holding an envelope AND a
            purchase inside it, which the accept door always refuses because
            the envelope's figure already covers its own purchases; without it
            the screen renders an Accept button that can never succeed.
        expected_on: The day the app PROJECTS this row on -- its pay period's
            start for a transaction, the purchase day for a purchase.  Two
            consumers, and for a PURCHASE it is one fact doing both jobs, which
            is why there is one field rather than two:

            * it makes "the bank never showed this" answerable for a row
              carrying no settle day.  A projection dated eighteen months out
              is not a payment the bank failed to make, and without it every
              undated row on the account joined that list -- 712 of them on the
              developer's own;
            * on a PURCHASE it is also a FLOOR.  A purchase cannot reach the
              bank before it was made, so ``update_entry`` refuses that write
              (``_reject_settled_before_purchase``) and a proposal pairing one
              with an earlier line is one the accept door always rejects --
              measured at 23 such pairs on the developer's own clone.
    """

    kind: RowKind
    row_id: int
    label: str
    cash_amount: Decimal
    settled_on: "date | None"
    is_settled: bool
    transfer_id: "int | None" = None
    parent_id: "int | None" = None
    expected_on: "date | None" = None


@dataclass(frozen=True)
class Candidates:
    """The rows a statement could be showing, and the ones nothing could price.

    Two facts that must travel together: a screen listing what it could match
    and saying nothing about what it could not read as a clean sweep, which is
    the "no silent caps" discipline applied to a money screen.

    Attributes:
        rows: The offerable candidates, transactions before purchases.
        unpriceable_ids: The transactions the amount model had no rule for.
            Empty on today's data -- every production row still owns its
            figure -- and live from the first per-kind cutover (plan step
            ``balance:X-au-d``).  They are NOT candidates: a matcher that
            offered a row it could not price would be guessing.
    """

    rows: "list[CandidateRow]"
    unpriceable_ids: "tuple[int, ...]"


@dataclass(frozen=True)
class BankLine:
    """One recorded statement line, as a proposal needs to show it.

    Attributes:
        line_id: The ``budget.bank_statement_lines`` row.
        posted_on: The day the bank posted it -- the fact this whole arc
            exists to obtain.
        amount: Signed, positive INTO the account.
        description: What the bank called it, verbatim.
    """

    line_id: int
    posted_on: date
    amount: Decimal
    description: str


@dataclass(frozen=True)
class MatchProposal:
    """A candidate correspondence the app OFFERS, never applies.

    Ruling **R-FP**: *a match is a PROPOSAL, never a silent apply*.  Nothing
    here is written anywhere; :func:`~._accept.accept_match` takes a
    :class:`MatchSubmission` built from the owner's own choice.

    Attributes:
        lines: The bank lines this proposal explains.  One for R-FS's first
            two shapes; several where N lines sum to one row.
        rows: The app rows it names.  One, or several where the app splits one
            movement.
        day_gap: The distance in days between the member rows' recorded
            ``settled_on`` and the day this proposal would move them to, or
            ``None`` when no member carries a day at all.  It is the field a
            reviewer scans: 0 confirms what the app already held, 8 corrects
            it, and ``None`` says the app never recorded the money as having
            moved -- three different acts, and a first draft collapsed the
            third into the first by reading "no distance" as "no difference".
    """

    lines: "tuple[BankLine, ...]"
    rows: "tuple[CandidateRow, ...]"
    day_gap: "int | None"

    @property
    def bank_amount(self) -> Decimal:
        """Return the signed total the bank states for this proposal."""
        return sum((line.amount for line in self.lines), Decimal("0.00"))

    @property
    def app_amount(self) -> Decimal:
        """Return the signed total the app currently holds for it."""
        return sum((row.cash_amount for row in self.rows), Decimal("0.00"))

    @property
    def difference(self) -> Decimal:
        """Return what the bank states MINUS what the app holds.

        ``0.00`` for a proposal that balances.  Non-zero is not a rounding
        detail to be absorbed: measured on the developer's own statement, 6 of
        16 payroll deposits sit `$0.05`-`$0.06` apart from the app's own rows,
        which is finding **N-299** and is why
        :func:`~._accept.accept_match` refuses rather than apportions.
        """
        return self.bank_amount - self.app_amount

    @property
    def confirms(self) -> bool:
        """Return whether this proposal changes no member's recorded day.

        The template's own question, answered here rather than as a truth test
        on :attr:`day_gap` -- where ``None`` reads falsy and an unsettled row
        would be captioned as confirming a day it never had.
        """
        return self.day_gap == 0

    @property
    def posts_on(self) -> date:
        """Return the day every member row would take.

        **The LATEST of the proposal's bank days**, and the choice matters
        where several lines sum to one row.  A row is not wholly moved until
        its last line posts, so the earliest day would let a balance asserted
        between the two absorb money that had not all left the account --
        which is the class of double-count ``dated_deltas``' day partition
        exists to make unspellable.  With one line, which is every proposal
        this app offers automatically, the two rules agree.
        """
        return max(line.posted_on for line in self.lines)


@dataclass(frozen=True)
class MatchSubmission:
    """What the owner accepted: ids only.

    Deliberately carries no amount and no day.  Everything the accept door
    needs it re-derives from the rows the ids name, inside the same
    transaction, so a stale screen cannot commit a figure the database no
    longer holds -- the same reason
    :func:`~app.services.reconcile_service._rows.record_settled` re-derives its
    ids through the arm's own scope rather than trusting them.

    Attributes:
        owner_id: The user the route proved owns the account.
        account_id: The account both sides must belong to.
        line_ids: The bank lines to explain.
        transaction_ids: The transactions that explain them.
        entry_ids: The purchases that explain them.
    """

    owner_id: int
    account_id: int
    line_ids: "frozenset[int]"
    transaction_ids: "frozenset[int]"
    entry_ids: "frozenset[int]"
