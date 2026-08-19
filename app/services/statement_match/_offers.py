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
import re
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

    Pylint: too-many-instance-attributes -- **ten fields because the subject
    genuinely has ten**, not because the value wants splitting.
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
        expected_on: The FIRST day the app believes this row's money could
            have moved -- its pay period's start for a transaction, the
            purchase day for a purchase.  Three consumers, and for a PURCHASE
            it is one fact doing all three jobs, which is why there is one
            field rather than three:

            * it makes "the bank never showed this" answerable for a row
              carrying no settle day.  A projection dated eighteen months out
              is not a payment the bank failed to make, and without it every
              undated row on the account joined that list -- 712 of them on the
              developer's own;
            * on a PURCHASE it is also a FLOOR.  A purchase cannot reach the
              bank before it was made, so ``update_entry`` refuses that write
              (``_reject_settled_before_purchase``) and a proposal pairing one
              with an earlier line is one the accept door always rejects --
              measured at 23 such pairs on the developer's own clone;
            * it opens :attr:`expected_window`, which is what BOUNDS a row the
              app has never settled.
        expected_through: The LAST such day -- the pay period's END for a
            transaction, and the purchase day again for a purchase, whose
            budget clock is a single day rather than a span.  **It is the half
            that was missing, and its absence had no bound at all**: plan step
            ``bank_import:X-f6a-3c``, finding **N-312**.  See
            :attr:`expected_window`.
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
    expected_through: "date | None" = None

    @property
    def expected_window(self) -> "tuple[date, date] | None":
        """Return the days the app believes this row's money moved between.

        **The one place "when does the app think this happened" is answered**,
        because the answer differs by row kind and by whether the row has been
        settled, and stating it at each asking site is how a whole kind came to
        have no answer at all.

        * a SETTLED row is a point: ``settled_on`` is an OBSERVATION, and an
          observation beats a belief, so the projection is not consulted;
        * a PURCHASE is a point at ``purchased_on``.  Every purchase has one
          -- ``transaction_entries.purchased_on`` is NOT NULL -- so "undated"
          is true of a purchase's CASH clock and false of the purchase.  Plan
          step ``bank_import:X-f6a-3a``, ruling **R-FW**;
        * a TRANSACTION is its PAY PERIOD, start to end.  Its ``expected_on``
          alone is a budgeting fact rather than an observation, so a rule
          reading it as a point would be claiming the app knows a day it does
          not; the period is the span the app actually asserts, and it is the
          whole of what it asserts.

        **The bound this produces is CADENCE-RELATIVE, and saying so is part
        of stating it.**  ``budget.pay_schedule.cadence_days`` is
        user-selectable 1..365, so the days a line may be posted on and still
        claim a bill run to the period's length plus twice
        :data:`~._propose.DAY_WINDOW`: 35 for a weekly owner, 42 for the
        biweekly one this was measured against, 58 monthly, and 393 at an
        annual cadence -- where it is barely a bound at all.  That is the
        honest consequence of bounding a row by what the app itself asserts: an
        owner who budgets in coarser blocks has asserted less about when the
        money moves, and inventing a tighter claim on their behalf is the
        substitution ruling **R-FW** rejected one clock over.  What keeps it
        safe at every cadence is that a proposal is reviewed before it commits
        (**R-FP**).

        **A TRANSACTION answered ``None`` here until plan step X-f6a-3c, and
        that was finding N-312: a bill the app has never marked as paid could
        be claimed by a bank line of any date whatever.**  Measured on the
        developer's own clone: 610 unsettled transactions, 600 of them
        projections dated past the statement's last day, and when the settled
        partner is removed from an amount group **44 of the statement's own
        lines immediately pair with a future projection** -- the worst a
        2026-04-01 line taking a mortgage transfer budgeted 2026-08-27, 148
        days later.  It never fired on the first import only because a settled
        row won every amount race; the second import is where the app's own
        rows have run out.  The earlier reasoning -- that bounding a bill would
        refuse the arm which settles a row nobody has marked as having happened
        -- was re-measured and does not hold: every one of the 51 rows that arm
        settles today is a PURCHASE, already bounded by its own day, and 0
        proposals name an unsettled transaction on either the first pass or the
        second.

        Returns:
            ``(first, last)``, or ``None`` for a row the app can date no way at
            all -- which the proposer reads as NOT OFFERABLE rather than as
            unbounded (:func:`~._propose._within_window`).  A row stating only
            :attr:`expected_on` is read as a POINT rather than as unbounded for
            the same reason, so a half-stated window is always TIGHTER than a
            whole one and never looser: that is the direction a missing fact
            has to fail in on a money path.
        """
        if self.settled_on is not None:
            return (self.settled_on, self.settled_on)
        if self.expected_on is None:
            return None
        return (self.expected_on, self.expected_through or self.expected_on)


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


#: The merchant SECU puts in PARENTHESES at the end of a description cell.
#: A delimited trailing token, exactly like the ``DATE MM-DD`` field
#: ``_secu_csv._stated_transaction_day`` reads, and better covered: it is
#: present on **361 of 361** of the developer's recorded lines where the stated
#: day is on 182, and 0 of those lines carry the ``Description | Memo`` join at
#: all, so the token is the source's own field rather than a user's free text.
_MERCHANT = re.compile(r"\(([^()]{1,100})\)\s*$")

#: What the SECU CSV reader puts between a line's description and the user's own
#: memo (``_secu_csv``).  Its presence is what makes a trailing parenthesis
#: ambiguous, so :func:`merchant_of` declines rather than guessing whose it is.
_MEMO_JOIN = " | "


def merchant_of(description: str) -> str:
    """Return what the bank called the merchant, else the whole description.

    **A DISPLAY default and never logic**, which is the distinction that makes
    reading a token out of a text column legitimate here.  Nothing branches on
    this: it prefills the name box on the new-envelope arm and names the
    purchase :mod:`._create` writes, both of which the owner can edit
    afterwards, and the bank's full description stays on the statement line
    forever with the match relation tying the two together.  A wrong parse
    costs a badly-named row, never a figure.

    **It is TOTAL, which is what lets a second adapter reach it.**  A source
    whose descriptions carry no such token -- SECU's own OFX truncates 326 of
    361 to 32 characters and would have no room for one -- falls back to the
    description itself, which is the honest answer rather than an empty name a
    NOT NULL column would then refuse.

    Args:
        description: The recorded line's description, verbatim.

    Returns:
        The bank's merchant where this description states exactly one
        unambiguously -- a single parenthesised trailing token, no memo, not
        blank -- else *description* unchanged.
    """
    if _MEMO_JOIN in description:
        # A MEMO is the user's own free text, appended by the adapter after
        # ``|``.  Its parentheses are indistinguishable from the bank's, so a
        # memo ending "(anything)" would become the merchant and the envelope
        # name.  ``_secu_csv._stated_transaction_day`` makes the same bound
        # STRUCTURAL by reading the Description CELL; this reader only has the
        # joined column, so it declines instead.  0 of the developer's 361
        # lines carry a memo, which is a fact about today's data and not the
        # bound.  Found by adversarial financial review 2026-08-19.
        return description
    found = _MERCHANT.findall(description)
    # EXACTLY one, for the reason ``_stated_transaction_day`` gives for its own
    # token: with two, which one is "the" merchant is a guess.  A first version
    # used ``search`` and silently took the LAST.
    if len(found) != 1:
        return description
    merchant = found[0].strip()
    # An all-whitespace token is not a name.  ``transaction_entries.description``
    # is NOT NULL and this door calls ``create_entry`` directly, so no schema
    # length rule stands behind it.
    return merchant or description


@dataclass(frozen=True)
class BankLine:
    """One recorded statement line, as a proposal needs to show it.

    Attributes:
        line_id: The ``budget.bank_statement_lines`` row.
        posted_on: The day the bank posted it -- the fact this whole arc
            exists to obtain.
        amount: Signed, positive INTO the account.
        description: What the bank called it, verbatim.
        transaction_on: The day the bank STATED the transaction happened, or
            ``None`` where the source states none.  It is what a match writes
            onto a matched purchase's ``purchased_on`` (ruling **R-FW**), so it
            is carried here rather than re-read at the write door: the
            proposer has to know it too, because whether that write can
            succeed is what decides whether the pairing may be OFFERED.
    """

    line_id: int
    posted_on: date
    amount: Decimal
    description: str
    transaction_on: "date | None" = None

    @property
    def merchant(self) -> str:
        """Return what the bank called the merchant (:func:`merchant_of`)."""
        return merchant_of(self.description)

    @property
    def happened_on(self) -> date:
        """Return the day the bank says this movement was MADE.

        The stated transaction day where the bank states one, else the day it
        posted -- which is the tightest bound the statement supports, since
        money cannot clear before it moves.  **It is a fallback and not a
        claim of equality**, which is exactly the distinction
        ``bank_statement_lines.transaction_on`` became NULLABLE to express:
        callers that must write a day get an answer here, and callers that
        need to know whether the bank OBSERVED it read the column itself.
        """
        return self.transaction_on or self.posted_on


@dataclass(frozen=True)
class MatchDays:
    """The two days a match writes, derived once from its lines.

    Ruling **R-FV** gave a match one day to write and **R-FW** gives it a
    second, because a purchase carries two clocks and the bank states both.
    They are ONE value because they are derived from one set of lines and must
    not be re-derived per member: a match taking ``max(posted_on)`` for one row
    and recomputing it for the next would be two answers to one question.

    **It lives here, beside the values, because two modules ask it** -- the
    accept door, which writes the days, and :class:`MatchProposal`, which has
    to tell the reviewer what accepting would do.  A second spelling of "which
    day does this match write" is this arc's own root cause 1 on a money rule.

    Attributes:
        posts_on: The day every member row records the money as having moved --
            the LATEST of the match's bank days, for the reason
            :attr:`MatchProposal.posts_on` states.
        happened_on: The EARLIEST day the bank says any of these lines was
            MADE, which is what a matched purchase's ``purchased_on`` may be
            corrected to.  **Earliest against latest, and the asymmetry is the
            point**: a row is not wholly moved until its last line posts, and a
            purchase the bank split across several lines was made no later than
            the first of them.
        posted_first: The EARLIEST day any of these lines posted, which is what
            REFUTES a recorded purchase day (:func:`corrected_purchase_day`).
            A third day rather than a reuse of :attr:`posts_on`, for the same
            reason ``happened_on`` is one: money cannot leave before it is
            spent, so a purchase explained by lines posted 06-01 and 06-10 was
            made on or before 06-01 -- and testing a purchase recorded 06-05
            against 06-10 leaves an impossibility uncorrected, which
            ``update_entry``'s own check (against the LATEST day) would not
            catch either.  Found by adversarial design review 2026-08-18.
    """

    posts_on: date
    happened_on: date
    posted_first: date

    @classmethod
    def of(cls, lines) -> "MatchDays":
        """Return the days *lines* state.

        Args:
            lines: The match's bank lines, at least one.  **Structurally
                typed**: each must expose ``posted_on`` (a ``date``) and
                ``transaction_on`` (``date | None``), which both
                :class:`BankLine` and
                :class:`~app.models.statement_import.BankStatementLine` do.
                The idiom is
                :func:`app.services.cash_ledger._amounts._entry_checking_impact`'s
                -- one rule, stated once, over whichever of the two shapes the
                caller already holds, rather than a conversion whose only job
                is to satisfy an annotation.

        Returns:
            Its :class:`MatchDays`.
        """
        return cls(
            posts_on=max(line.posted_on for line in lines),
            happened_on=min(
                line.transaction_on or line.posted_on for line in lines
            ),
            posted_first=min(line.posted_on for line in lines),
        )


def corrected_purchase_day(
    row: CandidateRow, days: MatchDays,
) -> "date | None":
    """Return the day this purchase should be re-dated to, or ``None``.

    **Ruling R-FW: the bank owns both of a purchase's days, but it corrects
    only the day it CONTRADICTS.**  A purchase carries the day it was made
    (``purchased_on``, the budget clock) beside the day the bank took the money
    (``settled_on``, the cash clock).  Accepting a match asserts that this bank
    line IS this purchase -- which asserts the purchase was made on or before
    the day the line posted.  Where the app's recorded day is AFTER that, the
    owner's own assertion has refuted it and it moves; where it is not, the
    bank contradicts nothing and nothing moves.

    **The alternative was measured and it is worse.**  Taking the bank's day
    unconditionally would move 27 of the 44 purchases in today's proposals on
    the developer's own statement, and 18 of those would move LATER: their
    recorded day is already earlier than the bank's, because the bank states no
    transaction day on 179 of 361 lines and :attr:`BankLine.happened_on` then
    falls back to the CLEARING day.  Writing that would record a card purchase
    as having been made on the day it cleared -- replacing 27 dates the owner
    got right in order to fix 3 they got wrong.  Correcting only what is
    contradicted moves exactly those 3, and every one is an impossibility
    rather than a disagreement.

    **Only a PURCHASE has this second day.**  A transaction's ``settled_on`` is
    its only clock, and its pay period -- not a date column -- is what says when
    it was budgeted.

    Args:
        row: The member being moved.
        days: The days the match's lines state.

    Returns:
        The day to write into ``purchased_on``, or ``None`` when the bank
        contradicts nothing and the column must be left alone.
    """
    # ``expected_on is None`` is UNREACHABLE for a purchase and is stated
    # anyway, which is the discipline ``_candidates._transaction_candidates``
    # applies to its own shadow-parent clause: ``transaction_entries
    # .purchased_on`` is NOT NULL and ``_purchase_candidates`` always fills the
    # field, so the test can refuse no row today -- and without it a hand-built
    # candidate would reach a ``None`` comparison and raise ``TypeError`` from
    # inside a money path rather than declining.  Named by adversarial
    # test-quality review 2026-08-18, which measured that deleting it changes
    # no test.
    if row.kind is not RowKind.PURCHASE or row.expected_on is None:
        return None
    if row.expected_on <= days.posted_first:
        return None
    # Refuted.  The bank's own stated day where it has one, else the day it
    # posted -- the tightest day the owner's own assertion supports.  It is NOT
    # clamped to ``posts_on``: a source whose stated transaction day is LATER
    # than its posting day exists (2 of 361 OFX lines), and clamping would
    # invent a day to keep a write door quiet.  ``update_entry`` refuses that
    # pair by name, and the proposer already declines to offer it.
    return days.happened_on


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
        which is finding **N-239** and is why
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
    def days(self) -> MatchDays:
        """Return the two days accepting this proposal would write.

        The SAME derivation the accept door runs, so the screen cannot promise
        one thing and the write do another.
        """
        return MatchDays.of(self.lines)

    @property
    def redated_purchases(self) -> "tuple[CandidateRow, ...]":
        """Return the member purchases whose PURCHASE day accepting would move.

        The screen's own question, answered by
        :func:`corrected_purchase_day` rather than by a second date test in a
        Jinja condition -- where the rule would be stated twice and the two
        would diverge the first time either changed.

        Empty for every proposal that only moves a posting day, which is most
        of them: measured on the developer's own statement, 3 of the 44
        purchases in today's proposals are re-dated and 41 are not.
        """
        days = self.days
        return tuple(
            row for row in self.rows
            if corrected_purchase_day(row, days) is not None
        )

    @property
    def made_on(self) -> date:
        """Return the day a re-dated purchase would be moved to.

        Meaningful only when :attr:`redated_purchases` is non-empty; it is the
        earliest day the bank states for this proposal's lines.
        """
        return self.days.happened_on

    @property
    def redate_gap(self) -> "int | None":
        """Return the FURTHEST a purchase day would move, in days.

        **The screen named the day a purchase moves TO and never the day it
        moves FROM**, on the one write a release cannot undo -- so a reviewer
        was shown "corrects 1 purchase date(s) to 2026-05-30" with nothing
        saying the app currently holds 2026-07-27.  The posting-day caption
        beside it has always stated its distance; this is that caption's twin.
        Found by adversarial financial review 2026-08-18.

        ``None`` when nothing would be re-dated.
        """
        moved = self.redated_purchases
        if not moved:
            return None
        return max(
            (row.expected_on - self.days.happened_on).days for row in moved
        )

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

        **It DELEGATES rather than restating the rule.**  The template renders
        this property and the accept door writes :attr:`MatchDays.posts_on`, so
        a second spelling here would let the screen print one day while the
        door wrote another -- the duplication :class:`MatchDays` exists to
        prevent.  Found by two adversarial reviews 2026-08-18.
        """
        return self.days.posts_on


@dataclass(frozen=True)
class PurchaseDestination:
    """One budget line a bank line could BECOME a purchase against.

    Plan step ``bank_import:X-f6a-3b``.  The offered set is
    :func:`~._reads.destinations_for`'s, and it mirrors every guard
    ``entry_service.create_entry`` and :func:`~._accept.accept_match` apply --
    so the screen cannot render a destination whose submission is refused,
    which is the failure this arc has now fixed three times.

    Attributes:
        transaction_id: The budget line.
        label: What to call it, with its pay period's span, because the same
            envelope name recurs every period and a reviewer picking a
            destination for a May swipe has to see which May it is.
        pay_period_id: The period it is budgeted under, so a caller can offer
            the line's OWN period first without re-reading the calendar.
        is_settled: Whether it has already closed.  Adding to a closed row
            raises what that row RECORDS as its cost, which is a bigger thing
            to do than filling in an open budget, so the screen says which it
            is rather than leaving the reviewer to know.
    """

    transaction_id: int
    label: str
    pay_period_id: int
    is_settled: bool


@dataclass(frozen=True)
class NewEnvelope:
    """A budget line the import is being asked to CREATE for a bank line.

    Only the two facts an owner can state about spending the plan did not
    anticipate.  What it BUDGETS is not one of them -- nothing budgeted it, so
    the figure is ``0.00`` and :mod:`._create` writes it rather than accepting
    it, which keeps a form from proposing that unplanned spending was planned.

    Attributes:
        name: What to call the budget line.  Defaulted from what the bank
            called the merchant, and editable, because the bank's own words are
            the only description of this spending that exists.
        category_id: The owner's category it files under.  A REQUIRED choice
            and not a default: the bank's ``source_category`` is that bank's
            opinion about a merchant, governed by no ``ref`` table, and reading
            it as a Shekel category would be exactly the string-for-id
            substitution the project-wide reference rule forbids.
    """

    name: str
    category_id: int


@dataclass(frozen=True)
class PurchaseCreation:
    """What the owner submitted to turn one bank line into a purchase.

    Ids and a name, and deliberately no figure and no day: :mod:`._create`
    takes both days and the amount from the recorded LINE, inside the same
    transaction, so a stale page cannot commit a number the bank did not state.
    The same reason :class:`MatchSubmission` carries ids only.

    **Exactly one destination arm is set**, and :mod:`._create` refuses the
    other two shapes rather than preferring one -- a door that silently picked
    an arm would record something nobody asked for.

    Attributes:
        owner_id: The user the route proved owns the account.
        account_id: The account the line belongs to.
        line_id: The bank line to record.
        transaction_id: An existing envelope to put it in, or ``None``.
        new_envelope: An envelope to create for it, or ``None``.
    """

    owner_id: int
    account_id: int
    line_id: int
    transaction_id: "int | None" = None
    new_envelope: "NewEnvelope | None" = None


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
