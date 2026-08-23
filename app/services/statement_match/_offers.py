"""What a match IS, as values -- the shapes every other module here passes.

Ruling **R-FS** gives a match three shapes and this module gives them one
type.  A :class:`MatchProposal` is a candidate the app OFFERS; a
:class:`MatchSubmission` is what the owner sent back.  They are deliberately
different types rather than one reused both ways: a proposal carries what the
screen needs to explain itself, and a submission carries only ids, so nothing
a user posts can smuggle a figure the app computed.

**What a CREATION is lives in** :mod:`._creations` **since plan step
``bank_import:X-f6d-1``**, and the seam is the subject rather than the line
count: this module is about a correspondence between what the bank recorded
and what the app already holds, and those five names are about a budget row
that does not exist yet.  Five of the six modules that import them import
this one too, and that is the honest statement: the seam is the
SUBJECT, not the import graph -- a review screen is about both.

Services-boundary discipline (``CLAUDE.md`` Architecture): frozen dataclasses,
no Flask import, no query, no clock read.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.enums import SettledDayBasisEnum


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

    Pylint: too-many-instance-attributes -- **twelve fields because the subject
    genuinely has twelve**, not because the value wants splitting.
    It describes ONE row drawn from either of two tables, for five consumers
    that each read a different subset: the proposer reads the amount, the days
    and whether the bank's own figure could be written here, the assignment
    reads the days, the screen reads the label and the kind, the accept door
    reads the kind, the id and the routing links, and the unmatched-rows panel
    reads the projection day.  ``TransferSpec`` carries the same disable for
    the same reason.  Two fields were MERGED rather than disabled around:
    ``earliest_day`` and ``expected_on`` were one fact for the only kind that
    has both.

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
        states_own_figure: Whether this row's amount is a fact about THIS row
            rather than about some other one.  ``False`` for the two shapes
            ``transaction_service`` publishes a predicate for -- an ENVELOPE
            whose figure is its purchases (``settles_from_entries``) and a CC
            PAYBACK whose figure is the card spend it repays
            (``repays_card_spend``) -- and ``True`` for every purchase, which
            stores its own.

            **It is CARRIED rather than re-asked, because two modules need one
            answer** (plan step ``bank_import:X-f6d-1``).  The accept door has
            always asked it, to refuse a correction the next sibling write
            would silently revert (finding **N-252**); the PROPOSER now needs
            the same fact, because a near miss it offers on such a row is an
            Accept button that can never succeed -- and the proposer is pure,
            with no session to ask.  Carrying it is the shape
            :attr:`settle_day_basis` beside it took for the same reason: a
            fact the row states once, read by whoever needs it, rather than
            two derivations that can disagree.  See
            :attr:`figure_is_correctable`, which is the question those two
            modules actually ask.
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
        settle_day_basis: WHICH KIND of day :attr:`settled_on` is, read
            straight off ``settled_day_basis_id``
            (:class:`app.enums.SettledDayBasisEnum`): ``asserted`` is the
            reconcile panel's UPPER BOUND, ``observed`` is a day a bank
            statement showed, ``entered`` is the owner's own.  ``None`` exactly
            when :attr:`settled_on` is.  The three settle days this package can
            meet are not the same kind of fact and the difference decides a
            window, which is why the basis travels rather than being re-derived
            per asking site.  See :attr:`expected_window`.

            **It was a BOOLEAN derived from ``reconciled_by_id`` until plan step
            X-az** (finding **N-332**), and the column it was derived from
            answered a different question -- WHICH statement was seen to show
            this money, not what kind of day the row records.  The two agreed by
            coincidence of the writers that existed: exact over the panel's
            bound and the bank's observation, and BLIND to the third case, so a
            day the owner typed read as a day the bank had shown.  Carrying the
            basis is not a wider boolean, it is the fact itself.
    """

    kind: RowKind
    row_id: int
    label: str
    cash_amount: Decimal
    settled_on: "date | None"
    is_settled: bool
    states_own_figure: bool
    transfer_id: "int | None" = None
    parent_id: "int | None" = None
    expected_on: "date | None" = None
    expected_through: "date | None" = None
    settle_day_basis: "SettledDayBasisEnum | None" = None

    @property
    def expected_window(self) -> "tuple[date, date] | None":
        """Return the days the app believes this row's money moved between.

        **The one place "when does the app think this happened" is answered**,
        because the answer differs by row kind and by whether the row has been
        settled, and stating it at each asking site is how a whole kind came to
        have no answer at all.

        * a row settled by an OBSERVED day is a point: that ``settled_on`` is
          the day a statement showed the money moving, and an observation beats
          a belief, so the projection is not consulted;
        * a PURCHASE settled by the RECONCILE PANEL spans ``purchased_on`` to
          ``settled_on``, because that day is a BOUND and not an observation --
          see the measurement below.  A BILL ticked the same way keeps its
          point for now, which the same passage explains;
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
        :data:`~._pairing.DAY_WINDOW`: 35 for a weekly owner, 42 for the
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

        **An ASSERTED settle day is a BOUND, and reading it as a point made
        the matcher blind to the rows it most needed to see.**  The reconcile
        panel stamps the day the owner asserted the BALANCE for --
        ``reconcile_service._purchases.record_settled_days`` says so in as many
        words, *"``settled_on`` is an UPPER BOUND on the true posting day"* --
        while this property read every settle day as the day a statement
        showed.  Two packages, one column, two meanings.

        **The row now SAYS which it holds** (plan step X-az, finding **N-332**).
        This branch asked ``reconciled_by_id IS NOT NULL`` until then, which is
        a different question -- WHICH statement was seen to show this money --
        and it happened to answer the same way for the two writers it met.  It
        could not see the third: a day the owner typed carries no link, so it
        read as a bank observation and got a point.  ``settled_day_basis_id`` is
        the fact itself, and the branch names the member it is about.  Measured on the
        developer's own dev database 2026-08-21: all 61 reconciled purchases on
        Checking carry ``settled_on = 2026-08-18``, and **59 of them sit more
        than** :data:`~._pairing.DAY_WINDOW` **days after their purchase day**
        (worst: 128).  So a point at ``settled_on`` put them out of reach of
        their own bank lines, every such line read as unexplained, and the
        merchant-destination policy offered to RECORD it -- **50 duplicate
        purchases worth `$3,590.00`**, among them a `$18.64` Food Lion the app
        already held on the bank's own day.  The remedy is to say what the app
        actually knows: the money moved between the day the row was budgeted
        for and the day the balance was asserted.

        **PURCHASES only, and that is a measured scope rather than a
        half-finished one** (developer decision 2026-08-22).  The panel stamps
        bills and transfer shadows identically, so the argument for widening
        them is the same -- but the EVIDENCE is not, and neither is the risk.
        ``budget.transactions`` carries **zero** reconciled rows today, so that
        arm would ship on argument alone; and a purchase's floor is a database
        fact (``ck_transaction_entries_settled_not_before_purchase``) while a
        bill's is its pay-period start, which this docstring calls a budgeting
        fact and refuses to read as a point *for that very reason*.  Widening a
        bill therefore opens a span with no floor and no cost signal: a
        `$1,910.95` payment budgeted 2026-01-05..01-18 and reconciled 08-18
        spans **225 days** in which :func:`~._pairing.days_outside` scores
        every day zero, so March, May and July lines of that same amount all
        become legal top-ranked pairings -- against a
        :data:`~._pairing.DAY_WINDOW` measured at 14 precisely so *"a monthly
        commitment cannot reach its neighbour"*.  Found by two independent
        adversarial reviews 2026-08-22.

        **The bound is applied only when it TIGHTENS nothing away.**  A
        reconciled row whose ``expected_on`` falls after its ``settled_on``
        keeps the point: the panel would then be asserting the money moved
        before the app expected it, which bounds the span from ABOVE and says
        nothing about its floor, and inventing one would be the looser reading
        this property refuses everywhere else.  **That branch is UNREACHABLE
        through every door today** and is kept for the reason
        :func:`corrected_purchase_day` keeps its own: a total accessor states
        its impossible case rather than trusting the callers who make it so.
        ``ck_transaction_entries_settled_not_before_purchase`` makes
        ``purchased_on <= settled_on`` a database fact, so no purchase can
        reach it; a pay-period ``start_date`` edit in the ``pay_calendar`` arc
        is what would.

        Returns:
            ``(first, last)``, or ``None`` for a row the app can date no way at
            all -- which the proposer reads as NOT OFFERABLE rather than as
            unbounded (:func:`~._pairing.within_window`).  A row stating only
            :attr:`expected_on` is read as a POINT rather than as unbounded for
            the same reason, so a half-stated window is always TIGHTER than a
            whole one and never looser: that is the direction a missing fact
            has to fail in on a money path.
        """
        if self.settled_on is not None:
            if (
                self.kind is RowKind.PURCHASE
                and self.settle_day_basis is SettledDayBasisEnum.ASSERTED
                and self.expected_on is not None
                and self.expected_on <= self.settled_on
            ):
                return (self.expected_on, self.settled_on)
            return (self.settled_on, self.settled_on)
        if self.expected_on is None:
            return None
        return (self.expected_on, self.expected_through or self.expected_on)

    @property
    def figure_is_correctable(self) -> bool:
        """Return whether a bank line's own figure may be WRITTEN to this row.

        **The one statement of "could the accept door take a variance here",
        and it exists because two modules ask it** (plan step
        ``bank_import:X-f6d-1``).  Ruling **R-GD(a)** made the bank's figure
        the record, so a one-to-one match whose sides disagree is RECORDED
        rather than refused -- but three row shapes are still refused, and a
        proposer blind to them would offer a near miss whose Accept can never
        succeed.  That is the shape this package has now named five times.

        The two facts it reads are the row's own, and each is stated once:

        * a transfer SHADOW cannot be corrected alone -- ``CLAUDE.md`` transfer
          invariant 3 holds its amount equal to its parent's, so the correction
          is to the TRANSFER.  :attr:`transfer_id` is that fact;
        * a row whose figure is not its own to state cannot be corrected at
          all, because the next sibling write reverts it (finding **N-252**).
          :attr:`states_own_figure` is that fact, and it is TWO published
          predicates rather than one -- see that attribute.

        **The FOURTH refusal is not here and must not be**: a group whose sides
        differ is refused for a reason that is not about any one row (nothing
        says WHICH member is wrong), so it belongs to the pass that builds the
        group.  The FIFTH, a sign disagreement, is a fact about the PAIR.

        **A PURCHASE gets no branch of its own**, and the omission is
        deliberate: it stores its own figure and belongs to no transfer, so
        both terms already answer for it and a short-circuit on
        :attr:`kind` would be a second spelling that could disagree with the
        first.  Ruling **R-GE** -- a statement's evidence justifies correcting
        a SETTLED purchase's amount -- bounds that permission by the DOOR
        rather than by the row, so nothing here narrows it either.

        Returns:
            Whether :func:`~._accept._reject_uncorrectable` would let a
            differing figure through for this row.
        """
        return self.transfer_id is None and self.states_own_figure


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


def merchant_label(line) -> str:
    """Return what to CALL this line's merchant, always non-empty.

    The bank's own merchant where the source names one
    (``bank_statement_lines.merchant``), else the whole description.  **Two
    consumers, and both need a string rather than an answer**: the new-envelope
    name box on the review screen, and the description
    :func:`~._create.create_purchase_from_line` gives the purchase it writes.
    ``transactions.name`` and ``transaction_entries.description`` are both NOT
    NULL, and neither door goes through a schema that would supply a default,
    so the fallback is what makes those writes total.

    **It is a LABEL and never a key**, which is the whole distinction plan step
    ``bank_import:X-f6a-3d`` drew: a merchant destination policy is keyed by
    the COLUMN, which is ``None`` for a source that names no merchant, so a
    policy fires on nothing there.  This falls back to the description instead,
    because a name box cannot show ``None`` -- and if the two were one
    function, that fallback would become a key and a whole truncated OFX
    statement would share it.  The predecessor
    (``merchant_of(description)``) WAS one function, parsing the description at
    render time; what replaced it is the adapter recording the fact and this
    reader choosing how to display it.

    **Structurally typed** over :class:`BankLine` and
    :class:`~app.models.statement_import.BankStatementLine` alike -- each
    exposes ``merchant`` and ``description`` -- which is
    :meth:`MatchDays.of`'s idiom: one rule, stated once, over whichever of the
    two shapes the caller already holds.

    Args:
        line: A recorded line, in either shape.

    Returns:
        The label.
    """
    return line.merchant or line.description


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
        merchant: What the bank NAMES the merchant, or ``None`` where the
            source names none.  **Carried from the column rather than parsed
            from :attr:`description`** (plan step ``bank_import:X-f6a-3d``):
            it is the key a destination policy is stated against, and a reader
            that derived it would have to be total, which on a source with no
            merchant field means every line keying one policy.
    """

    line_id: int
    posted_on: date
    amount: Decimal
    description: str
    transaction_on: "date | None" = None
    merchant: "str | None" = None

    @property
    def merchant_label(self) -> str:
        """Return what to call this line's merchant (:func:`merchant_label`)."""
        return merchant_label(self)

    @property
    def states_impossible_days(self) -> bool:
        """Return whether the bank dates this line MADE after it POSTED.

        Two of a source's own facts contradicting each other, which the schema
        deliberately admits: ``bank_statement_lines`` imposes no
        ``transaction_on <= posted_on`` CHECK because 2 of 361 lines in the
        developer's own OFX carry an ``DTUSER`` one day after their
        ``DTPOSTED``, and a constraint a real statement violates would make the
        truth unimportable.

        **So the guard is a reader's, and it is stated HERE because two readers
        ask it** (finding **N-325**).  :func:`~._pairing.within_window` asks it
        to decide whether a purchase recorded after the line posted may still
        be paired -- it may, because the bank's own stated day is later too --
        and :func:`~._reads._creatable_lines` asks it to decline OFFERING such a
        line as a purchase at all, because ``entry_service.create_entry``
        refuses a purchase whose money left before it was spent and the screen
        would be rendering a chooser whose submission can never succeed.  Two
        spellings of one predicate on a money screen is this arc's own root
        cause 1 -- and this sentence was FALSE when first written: the proposer
        went on spelling it inline, so the claim described an intention rather
        than the tree.  Both adversarial reviews of 2026-08-19 caught it.

        0 of the developer's 361 recorded lines are this shape; the OFX
        adapter's own measurement found 2 of 361, so a second source makes it
        live.
        """
        return (
            self.transaction_on is not None
            and self.transaction_on > self.posted_on
        )

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

        ``0.00`` for a proposal that balances.  Non-zero is what a NEAR MISS
        is, and ruling **R-GD(a)** made it the PRODUCT rather than a refusal:
        the bank's figure becomes the record and this is the correction
        accepting would write.  The screen states it before anything is
        pressed -- *bank `$178.29`, your row `$178.32`* -- which is plan step
        ``bank_import:X-f6d-1``'s own sentence.

        **It stays non-zero only where the door can honestly record it.**  A
        GROUP whose sides differ is still refused, because nothing says WHICH
        member is wrong -- measured on the developer's own statement, 6 of 16
        payroll deposits sit `$0.05`-`$0.06` apart from the app's own rows,
        which is finding **N-239** and is `X-f6d-4`'s subject rather than a
        figure this pass invents for a member.
        """
        return self.bank_amount - self.app_amount

    @property
    def reprices(self) -> bool:
        """Return whether accepting would change an AMOUNT and not only a day.

        The template's own question, answered here rather than as a
        ``difference != 0`` test in a Jinja condition -- the rule
        :attr:`review_class` states for the partition it heads, applied to the
        term that partition now turns on.
        """
        return self.difference != 0

    @property
    def confirms(self) -> bool:
        """Return whether this proposal changes no member's recorded day.

        The template's own question, answered here rather than as a truth test
        on :attr:`day_gap` -- where ``None`` reads falsy and an unsettled row
        would be captioned as confirming a day it never had.
        """
        return self.day_gap == 0

    @property
    def review_class(self) -> str:
        """Return which of four things accepting this proposal would DO.

        ``"reprice"`` when the two sides state different figures,
        ``"confirm"`` when it changes no recorded day, ``"correct"`` when it
        moves one the app had wrong, ``"settle"`` when no member carries a day
        at all and the match is what marks the money as having moved.

        **A PARTITION, and that is what the review screen's sweep controls
        rest on** (plan step ``bank_import:X-f6a-3c-2``, developer ruling
        2026-08-19).  R-FP's *reviewed before it commits* survives 124
        proposals only if the sweep is per class rather than one "tick all":
        the classes are different acts with different consequences, so the
        riskiest is never swept by the same click as the safest.  Measured on
        the developer's own statement, the three day classes came to
        27 / 46 / 51 of 124 and they sum -- which is the property a caption
        counting them has to be able to rely on.

        **``"reprice"`` is the fourth member and it takes PRECEDENCE, on
        ruling R-FZ(c)'s own criterion** (plan step ``bank_import:X-f6d-1``,
        developer decision 2026-08-22).  A near miss moves an AMOUNT as well
        as a day, which is the only act on this card that changes what money
        was spent; classing it by its day effect alone would put it on the
        same "tick all" checkbox as 104 day-only corrections, and *the
        riskiest class may not ride the same click as the safest* is the
        sentence that rules out exactly that.  The day effect is still printed
        per row, so nothing is hidden by the reclassification -- what changes
        is which sweep the proposal answers to.

        It is derived HERE rather than as a Jinja condition for the reason
        :attr:`confirms` is: ``day_gap`` is three-valued, and a template
        reading ``None`` as falsy would sweep the settle class in with the
        confirm class -- the exact collapse that caption was made three-valued
        to stop.
        """
        if self.reprices:
            return "reprice"
        if self.day_gap is None:
            return "settle"
        return "confirm" if self.day_gap == 0 else "correct"

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
class MatchSubmission:
    """What the owner accepted: ids only.

    Deliberately carries no amount and no day.  Everything the accept door
    needs it re-derives from the rows the ids name, inside the same
    transaction, so a stale screen cannot commit a figure the database no
    longer holds -- the same reason
    :func:`~app.services.reconcile_service._rows.record_settled` re-derives its
    ids through the arm's own scope rather than trusting them.

    **It names no OWNER and no ACCOUNT**, for the reason
    :class:`PurchaseCreation` states: whose account this is, is the
    :class:`~._scope.ReviewScope`'s, and a second statement of it could
    disagree with the scope the rows were priced from.

    Attributes:
        line_ids: The bank lines to explain.
        transaction_ids: The transactions that explain them.
        entry_ids: The purchases that explain them.
    """

    line_ids: "frozenset[int]"
    transaction_ids: "frozenset[int]"
    entry_ids: "frozenset[int]"
