"""What a CREATION is, as values -- a bank line BECOMING a budget row.

Ruling **R-FS**'s THIRD shape, split out of :mod:`._offers` at plan step
``bank_import:X-f6d-1``.  That module answers *what a MATCH is* -- a
correspondence between lines the bank recorded and rows the app already holds
-- and these six names answer a different question: *what the owner may ask
this import to CREATE*, and where.  Two subjects, two reasons to change, and
the package already draws that seam one tier up in :mod:`._accept` against
:mod:`._create`.

**The sixth is :class:`CreatedSubject`** (plan step ``bank_import:X-f6f``,
ruling **R-GG**), which is the other end of the same subject: the five values
above are what the owner may ASK for, and that one is what an act DID create,
carried to the write door so an undo can take it back.

**The split is a line cap made useful rather than worked around.**  Adding the
fact a scored near miss needs (:attr:`~._offers.CandidateRow.states_own_figure`)
took :mod:`._offers` past this project's 1,000-line module bound, and the two
honest answers to that are to cut the record or to cut the module.  Nothing
here changed on the way across.

**The argument is the SUBJECT and not the import graph, and a first draft of
this header got that wrong.**  It claimed the consumers were disjoint from the
matching ones; the tree refutes it -- five of the six modules that take a name
from here take one from :mod:`._offers` too, because a review screen is about
both a match and a creation.  An unmeasured claim in a header is this package's
own root cause 1, found by adversarial design review 2026-08-22.

Services-boundary discipline (``CLAUDE.md`` Architecture): frozen dataclasses,
no Flask import, no query, no clock read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ._offers import CandidateRow, RowKind


@dataclass(frozen=True)
class PurchaseDestination:  # pylint: disable=too-many-instance-attributes
    """One budget line a bank line could BECOME a purchase against.

    Plan step ``bank_import:X-f6a-3b``.  The offered set is
    :func:`~._reads.destinations_for`'s, and it mirrors every guard
    ``entry_service.create_entry`` and :func:`~._accept.accept_match` apply --
    so the screen cannot render a destination whose submission is refused,
    which is the failure this arc has now fixed three times.

    Pylint: too-many-instance-attributes -- **eight because a destination
    genuinely states eight things** (8/7), and three of them are what a merchant
    rule has to MATCH on rather than display: the name, the category, and
    whether a recurring definition owns the row.  ``StatementLine``,
    ``CandidateRow`` and ``CreatedPurchase`` carry the same disable for the same
    reason.

    Attributes:
        transaction_id: The budget line.
        name: The row's OWN name, unlabelled -- what a merchant rule's stated
            envelope name is compared against (plan step
            ``bank_import:X-f6a-4``, finding **N-327**), so that a second
            statement files into the envelope the first one created instead of
            minting another beside it.
        category_id: The category it files under.  **A rule's answer is a
            NAME AND a category and both are part of what it names**: two
            answers spelling one word under two categories are two budget
            lines, and reusing one for the other would file spending under a
            category the owner did not pick.  The within-press registry
            (:class:`~._create.MintedEnvelopes`) keys on it, and a first draft
            of the cross-statement half compared the name alone -- so the two
            halves of one rule disagreed, which is what this column being here
            makes impossible.
        period_start / period_end: Its pay period's span, from which
            :attr:`label` is derived.
        pay_period_id: The period it is budgeted under, so a caller can offer
            the line's OWN period first without re-reading the calendar.
        is_settled: Whether it has already closed.  Adding to a closed row
            raises what that row RECORDS as its cost, which is a bigger thing
            to do than filling in an open budget, so the screen says which it
            is rather than leaving the reviewer to know.
        template_id: The recurring definition this row was generated from, or
            ``None`` for an ad-hoc one.  **It is the row's identity ACROSS pay
            periods, and that is what a merchant rule is keyed
            on** (plan step ``bank_import:X-f6a-3d``): a rule cannot name
            ``transaction_id`` -- an envelope belongs to one period, and the 24
            unexplained Amazon lines on the developer's own statement fall in
            ten of them -- and it cannot name the NAME either, because template
            22 generated a row called ``Kayla`` in one period and ``Kayla's
            Spending Money`` in the other 60.  ``None`` is a real answer and it
            means this row can hold a purchase but can never be a RULE's
            destination, because there is nothing period-independent to
            remember about it.
    """

    transaction_id: int
    name: str
    category_id: int
    period_start: date
    period_end: date
    pay_period_id: int
    is_settled: bool
    template_id: "int | None" = None

    @property
    def label(self) -> str:
        """Return what to call this destination on screen.

        The same envelope name recurs every period, so a reviewer picking a
        destination for a May swipe has to see which May it is.

        **DERIVED rather than stored beside its source**, which is the rule
        :attr:`~._offers.MatchProposal.posts_on` states one class over: a
        second spelling could let the screen print one row's name while a
        rule matched another's.
        """
        return f"{self.name} ({self.period_start} - {self.period_end})"


def envelope_answer_key(
    new_envelope: NewEnvelope, pay_period_id: int,
) -> "tuple[str, int, int]":
    """Return what identifies ONE new-envelope answer in ONE pay period.

    **The one spelling of "the same envelope this answer means"**, and it lives
    here beside :class:`NewEnvelope` because the two callers are in different
    packages' modules: :class:`~._create.MintedEnvelopes` writes it as a press
    mints envelopes, and :func:`~._leftovers._marked_joining` reads it to say which
    line CREATES and which JOINS.  A first version spelled the tuple twice, in
    those two modules, which is exactly the silent drift a key stated once
    prevents -- a mismatch converges nothing and raises nothing.

    All three terms are load-bearing.  The NAME and the CATEGORY are what the
    owner's answer states, and two answers spelling one word under two
    categories are two budget lines.  The PERIOD is what an envelope belongs
    to, so converging on the name alone would file a March swipe into an April
    budget line.

    Args:
        new_envelope: The answer the owner stated.
        pay_period_id: The period the purchase is budgeted in.

    Returns:
        ``(name, category_id, pay_period_id)``.
    """
    return (new_envelope.name, new_envelope.category_id, pay_period_id)


#: What the destination select submits for the create-a-new-envelope arm, and
#: the ONE definition of it.
#:
#: **It lives in the service because the service PRODUCES it**:
#: :attr:`~._placement.Placement.select_value` answers what a line's control would
#: be set to, so the value is part of what this package says rather than only
#: something a schema reads.  ``app.schemas.validation.statements`` imports it
#: -- the direction that module already takes for
#: ``statement_import.supported_sources`` -- rather than declaring a second
#: literal, because two spellings of one wire value is a rule stated twice and
#: this package's own root cause 1.
NEW_ENVELOPE: str = "new"

#: What that same control submits for the RECORD-AS-INCOME arm, and the ONE
#: definition of it.  Plan step ``bank_import:X-gj-1b``, ruling
#: **bank_import:R-GW**.
#:
#: **A NAMED arm rather than an absence**, which is plan step X-f6a-3c-2's own
#: correction applied to the second ADD door: the Reconcile card's ADD tab
#: submits one field naming which of four things the owner meant -- leave it
#: alone, an existing envelope, a new one, or an income row filed against no
#: container at all -- and an arm read from a MISSING field is the shape that
#: made the existing-envelope arm unreachable from a browser once already.
#:
#: **It is not a destination and never reaches**
#: :class:`~app.schemas.validation.statements.StatementPurchaseSchema`:
#: ``reconcile_payload`` routes it to the income list, whose door
#: (:func:`~._income.record_income_from_line`) refuses any line that is not
#: money ARRIVING.  So a crafted body that swapped this value onto an outflow
#: is refused as that ITEM, with the door's own sentence, and the rest of the
#: pass still lands.
RECORD_AS_INCOME: str = "income"


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

    **It names no OWNER and no ACCOUNT** (plan step ``bank_import:X-f6a-3c-2``).
    Whose account this is, is the :class:`~._scope.ReviewScope`'s -- one
    statement, which the route proved once -- and a submission carrying its own
    pair was a second statement that could disagree with it: an item naming
    another account would have been priced from this scope and written against
    that one.  Unreachable through the route, which set both from the same
    verified ids, and unreachable is not the same as unspellable.  It is the
    rule :func:`~._candidates.candidates_for` already states for its own
    signature, applied one tier up.  Named by adversarial design review
    2026-08-19.

    Attributes:
        line_id: The bank line to record.
        transaction_id: An existing envelope to put it in, or ``None``.
        new_envelope: An envelope to create for it, or ``None``.
    """

    line_id: int
    transaction_id: "int | None" = None
    new_envelope: "NewEnvelope | None" = None


@dataclass(frozen=True)
class CreatedSubject:
    """One app row an act BROUGHT INTO EXISTENCE, and at which revision.

    Plan step ``bank_import:X-f6f``, ruling **R-GG**.  What a match act NAMES
    and what it MAKES are two relations
    (:class:`app.models.statement_match.StatementMatchCreation`), and this is
    the value the write door carries the second one in.

    **It is not a :class:`~._offers.CandidateRow`, and it cannot be.**  A
    candidate is a row a bank line could BE -- priced, dated and offerable --
    and the container this door may also create is none of those: an envelope
    holding one purchase that already carries its own posting day is worth
    ``0.00``, which is exactly the answer
    :func:`~._candidates.transaction_candidate` returns ``None`` for.  So the
    creation record takes the three facts it actually needs and no more.

    Attributes:
        kind: Which table the subject is in.
        row_id: Its primary key within that table.
        version_id: The subject's ``version_id`` as this act LEFT it, which is
            what lets the undo tell a row nobody has touched from one the
            owner has since made their own.  **As the act left it, not as the
            act found it**: a door that creates a row and then settles it has
            written twice, and recording the first revision would report its
            own second write as somebody else's edit.
    """

    kind: "RowKind"
    row_id: int
    version_id: int

    @classmethod
    def of(cls, row: "CandidateRow") -> "CreatedSubject":
        """Return the creation record for a subject the act also NAMES.

        Both created MEMBERS -- a group's residual and a purchase recorded
        from a bank line -- reach the write door as a priced candidate
        already, so their three facts are read from it rather than from the
        ORM row a second time.

        Args:
            row: The candidate the act created and is about to name.

        Returns:
            Its :class:`CreatedSubject`.
        """
        return cls(
            kind=row.kind, row_id=row.row_id, version_id=row.version_id,
        )


@dataclass(frozen=True)
class CreatedPurchase:  # pylint: disable=too-many-instance-attributes
    """What recording one bank line as a purchase did.

    Pylint: too-many-instance-attributes -- **nine because the act genuinely
    produces nine facts**, with four separate consumers reading disjoint
    subsets: the structured log takes the three ids and both days, the flash
    takes the container's label and whether it was created plus the figure and
    the posting day, the tests take the ids, and
    :meth:`MintedEnvelopes.remember` takes the period.  ``CandidateRow`` beside
    it carries the same disable for the same reason.  Splitting the container's
    fields into a nested value would be the speculative shape rule 13 forbids
    -- nothing asks for the container alone.

    Attributes:
        entry_id: The ``budget.transaction_entries`` row now holding the
            movement.
        transaction_id: The budget line that contains it.
        match_id: The ``budget.statement_matches`` act recording that this line
            IS that purchase, so the line stops being unexplained and a
            re-import does not re-offer it.
        envelope_label: What to call the container on screen.
        envelope_created: Whether that container was created by this act.  The
            receipt names it, because creating a budget line is a bigger thing
            to have done than filing a purchase under one that existed.
        amount: The purchase's own figure, POSITIVE -- what the bank took.
        posts_on: The day the bank took it.
        made_on: The day the bank says it was made, which is the purchase's own
            budget clock and is the posting day where the source states none.
        pay_period_id: The period the purchase is BUDGETED in, which is the
            period holding :attr:`made_on`.  Carried out rather than re-derived
            by a caller: it is resolved once here for both arms, and a second
            derivation is how the two came to disagree once already.  It is
            what :meth:`MintedEnvelopes.remember` keys the minted envelope by.
    """

    entry_id: int
    transaction_id: int
    match_id: int
    envelope_label: str
    envelope_created: bool
    amount: Decimal
    posts_on: date
    made_on: date
    pay_period_id: int


@dataclass(frozen=True)
class IncomeCreation:
    """What the owner submitted to record one bank line as an income row.

    Ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1``.  **One id
    and nothing else**, and the emptiness is the design rather than a stub: a
    purchase needs a container to be filed against and asks the owner which
    (:class:`PurchaseCreation`), while a deposit reserves nothing, so there is
    no arm to choose between and no name or category to state.  The figure and
    the day come from the recorded LINE inside the same transaction, so a
    stale page cannot commit a number the bank did not state.

    **It briefly carried a ``category_id`` and that was a defect** (plan step
    ``bank_import:X-gj-2a``, caught by adversarial code review 2026-08-31).
    Ruling **R-HT(a)** lets a standing rule say what a DEPOSIT is, and the
    first build put that answer here -- set by
    :meth:`~._placement.InflowPlacement.creation_for`, which the import-time
    rule pass reaches and no route does.  The Reconcile card therefore said
    *Add as Interest income* and its OK wrote an UNCATEGORIZED row: two
    answers to one question, on the door that moves money.  The classification
    is derived by :func:`~._income.record_income_from_line` from the stored
    rule now, so both consents reach ONE derivation and this value goes back to
    being the one id it was.

    **It is a value rather than a bare id, for the reason its sibling is one**:
    the batch carries two lists of acts, and a list of ints beside a list of
    submissions is a shape a caller can pass to the wrong door.  Whose account
    this is, is the :class:`~._scope.ReviewScope`'s -- one statement, which the
    route proved once.

    **The Reconcile card renders no category picker, and that is a developer
    ruling of 2026-08-31 rather than an omission.**  The rule is the answer, so
    the form posts one id and the door reads the rule.  A picker was considered
    and refused because a category typed at the card is a fact the app does not
    remember -- next month's deposit from the same signature would ask again,
    which is the ask-the-same-question-many-times shape ruling **R-GA** exists
    to remove.

    Attributes:
        line_id: The bank line to record.
    """

    line_id: int


@dataclass(frozen=True)
class RecordedIncome:
    """What recording one bank line as an income row did.

    Ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1``.  :class:`CreatedPurchase`
    without the container half and without the second clock: the row this act
    creates is filed under nothing and IS the movement, so it has neither a
    budget line to name nor a day it was "made" apart from the day the bank
    credited it.

    Attributes:
        transaction_id: The ``budget.transactions`` row now holding the
            movement, born uncategorized so the ledger books it to the
            per-owner Uncategorized fallback.
        match_id: The ``budget.statement_matches`` act recording that this line
            IS that row, so the line stops being unexplained and a re-import
            does not re-offer it.
        label: What the row is called on screen, as the candidate constructor
            renders it -- taken from the priced row rather than recomposed, so
            the receipt names what the grid will show.
        amount: The row's signed cash effect, POSITIVE -- what the bank paid
            in.  Already the bank's own direction, so a receipt prints it
            without a second convention.
        posts_on: The day the bank credited it, which is the row's settle day
            and the day its pay period is resolved from.
        pay_period_id: The period the row is budgeted in, which is the period
            holding :attr:`posts_on`.  Carried out rather than re-derived,
            because it was resolved once BEFORE the write and a second
            derivation is how two answers to one question get made.
    """

    transaction_id: int
    match_id: int
    label: str
    amount: Decimal
    posts_on: date
    pay_period_id: int
