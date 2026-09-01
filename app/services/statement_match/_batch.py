"""Apply everything the owner reviewed in ONE pass, and say what each item did.

Plan step ``bank_import:X-f6a-3c-2``, finding **N-306**.  The review screen
offers two acts -- accept a proposed match, record a bank line as a purchase --
and until this step each one was its own request through its own money door.
Measured on the developer's own 2026-08-16 statement against a production
clone: **124 proposals and 91 recordable lines, 215 round trips**, each paying
``candidates_for`` at **3.593 s** before it wrote a row.  **12.88 minutes of
derivation to work one statement**, which is why the corrections do not get
made.  The same 215 acts through this door, end to end and applied for real:
**5.80 s**, against **762.7 s** for a control that re-derives per act -- and
the two produce byte-identical outcomes on all 215 items and identical balances
on six sampled days.

**It is not "accept everything"** (ruling **R-FP**).  Nothing here decides
anything: every item in a batch is one the owner ticked, carrying the same ids
the screen showed, and every one of them goes through the same door, the same
refusals and the same settle verbs a single-item request always did.  What the
batch removes is the round trip, not the review.

**The failure policy is the developer's ruling of 2026-08-19**: a refused item
leaves nothing behind and the rest still land, each refusal quoted with its own
sentence.  It is not a hypothetical bound -- 5 of the developer's own 124
proposals refuse today and will keep refusing, all of the same CLASS: a settled
credit-card payback whose recorded figure has drifted from the card entries it
repays, so any later entry edit on its envelope is refused (finding **N-323**,
two paybacks, `$59.68` of drift).  A batch that failed whole would lose 119
good corrections to it.

**How each item is isolated: a SAVEPOINT.**  ``db.session.begin_nested()``
around each act, released when it lands and rolled back when it refuses.  The
REQUEST is still the transaction and the route still owns the commit, so a
batch that dies part-way commits nothing at all -- the savepoint bounds a
DESIGNED refusal, never a failure.

**A ``PostingError`` is not a refusal and is not caught.**  It means a ledger
invariant is broken, which is a fact about the account rather than about the
item, so it fails the whole request loud (``CLAUDE.md`` rule 4).

**Order: every match, then every create, each in the order it was submitted**
-- which is the order the screen renders them, so the receipt reads down the
page.  It is a real decision rather than an arbitrary one: two ticked items can
collide, because an envelope a match names may also be the destination a
recorded line was aimed at, and the guard against counting one purchase twice
(:func:`~._accept._reject_parent_and_its_own_purchase`) has to refuse one of
them.  Measured on the developer's own statement, 4 envelopes are both named by
a proposal and offered as a destination, and **15 of the 91 recordable lines
aim at one**.  The developer ruled 2026-08-19 that the PROPOSAL wins: it
explains money the records already hold against a line the bank showed, where
the recorded line can be re-aimed at another envelope on the next pass.

**Each item FLUSHES before the next is validated**, and that is what makes one
shared derivation safe rather than merely fast: the guard above reads the
database.

**It is NOT the only way one item can move a figure another item names, and
saying so was measured FALSE on 2026-08-19.**  Settling a matched purchase runs
``entry_service.update_entry``, which re-derives the envelope's CC Payback and
writes its ``estimated_amount`` -- a SIBLING rather than a child, invisible to
that guard.  What actually keeps a pass honest is that
:func:`~._candidates.repriced` re-prices every named row per act, and, since
plan step ``bank_import:X-f6d-3``, that an item whose row has moved since the
screen described it is REFUSED rather than written (finding **N-336**).  This
paragraph asserted the refuted reason until an adversarial review found it
2026-08-23; ``_reject_parent_and_its_own_purchase`` had already been corrected.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import.  It MUTATES and does NOT commit -- the
route owns the unit of work.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db

from ._accept import accept_match
from ._bars import MerchantAnswers
from ._create import MintedEnvelopes, create_purchase_from_line
from ._creations import IncomeCreation, PurchaseCreation
from ._income import record_income_from_line
from ._scope import ReviewScope
from ._submission import MatchSubmission


class Consent(enum.Enum):
    """WHO agreed to the acts in one batch (ruling **R-GH**).

    Plan step ``bank_import:X-ge``.  **Consent splits by ACT CLASS and a
    standing rule is consent**, so a pass through this door is one of exactly
    two things and never a mixture: a person read a screen and ticked, or a
    rule the owner stated earlier fired on lines an import had just recorded.

    **It is an enum rather than the boolean the column holds**, and the reason
    is that the two are not the same fact at the same grain.
    ``budget.statement_matches.applied_by_rule`` records what performed ONE
    act (ruling **R-GT**); this records what assembled a WHOLE pass, and it is
    the thing the pass's own refusal is keyed on
    (:meth:`ReviewedBatch.__post_init__`).  Deriving the column from it in one
    place -- :attr:`applied_by_rule` -- is what keeps a batch from being
    described one way and recorded another.
    """

    TICKED = "ticked"
    STANDING_RULE = "standing_rule"

    @property
    def applied_by_rule(self) -> bool:
        """Return what this consent writes to ``applied_by_rule``.

        The ONE mapping from a pass's consent to the column ruling **R-GT**
        stores per act, so a door cannot record a rule's work as a tick.
        """
        return self is Consent.STANDING_RULE


@dataclass(frozen=True)
class ReviewedBatch:
    """What the owner ticked, in the order the screen showed it.

    Ids and the state each row was REVIEWED in, exactly as
    :class:`~._submission.MatchSubmission` and
    :class:`~._creations.PurchaseCreation` are: every figure and every day this
    door WRITES is re-derived from the rows the ids name, inside the same
    transaction, so a stale page cannot commit a number the database no longer
    holds -- and since plan step ``bank_import:X-f6d-3`` an item whose row has
    MOVED since the screen described it is refused rather than written
    (finding **N-336**), which is the other half of the same sentence.

    **It names no OWNER and no ACCOUNT either**: whose pass this is, is the
    :class:`~._scope.ReviewScope`'s, stated once.  A batch carrying its own
    pair beside a scope carrying another was a second answer nothing
    reconciled.

    Attributes:
        matches: The proposals ticked, plus the hand-built group where one was
            submitted -- they are the same act and reach the same door, so they
            are one list rather than two.
        creations: The bank lines the owner named a destination for.
        incomes: The bank lines of money COMING IN the owner ticked to record
            (ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1``).  **Its own
            list beside *creations*, because they reach different doors and
            write different shapes**: a purchase is filed against a container
            the submission names, and an income row is filed against nothing
            and names only its line.  One list discriminated by the sign of a
            figure the submission does not carry would have to read the
            database to know which door an item meant.
        consent: Who agreed to these acts (:class:`Consent`, ruling **R-GH**).
            **Required, with no default**, for the reason
            :func:`~._accept.record_match` gives its own keyword-only flag: the
            two values are *the owner agreed to this* and *the app did it on
            their behalf*, and a default would let a door claim the first by
            omission -- which is consent laundered rather than recorded.
    """

    matches: "tuple[MatchSubmission, ...]"
    creations: "tuple[PurchaseCreation, ...]"
    incomes: "tuple[IncomeCreation, ...]"
    consent: Consent

    def __post_init__(self) -> None:
        """Refuse a rule pass carrying a MATCH.

        **It refused an INCOME too until plan step ``bank_import:X-gj-2a``**
        (ruling **R-HT(a)**); the comment below the match arm records why that
        sentence went false and why this one did not.

        **Ruling R-GH's boundary, made unrepresentable rather than maintained**
        (plan step ``bank_import:X-ge``).  A rule is consent for CREATING a row
        from a new bank swipe; every act that modifies a row the owner made by
        hand -- re-date, re-price, settle, group-match -- keeps its tick, and
        those are exactly the acts :func:`~._accept.accept_match` performs.
        That door hardcodes ``applied_by_rule=False`` and says no rule reaches
        it; this is the same sentence one tier up, where a caller could
        otherwise assemble the batch that contradicts it.

        A TICKED batch carrying no items at all is legal and ordinary -- it is
        what an untouched form posts -- so nothing here counts.

        **A rule pass may not carry an INCOME either** (ruling **bank_import:R-GW**,
        plan step ``bank_import:X-gf-1``), and that is a second sentence rather
        than a widening of the first: a merchant rule says where that
        merchant's SPENDING goes, so there is no answer it could hold that
        means *record this deposit*.  The door itself already refuses -- it is
        called with ``applied_by_rule=False`` as a literal -- and this is the
        same fact at the tier where a caller could assemble the batch that
        contradicts it, exactly as the match arm is.

        Raises:
            ValueError: When a :attr:`Consent.STANDING_RULE` batch names a
                MATCH.  **A programming error rather than a designed refusal**,
                so it is not a ``ValidationError``: no wire value reaches this
                field, the route states it as a literal, and there is no
                sentence to write for an owner who cannot have caused it.
        """
        if self.consent is not Consent.STANDING_RULE:
            return
        if self.matches:
            raise ValueError(
                "A standing rule may create a row from a new bank swipe and "
                "may not modify a row the owner made by hand (R-GH), so a "
                "rule-consented batch cannot carry a match."
            )
        # **A rule-consented batch MAY carry an income since plan step
        # ``bank_import:X-gj-2a``**, where this refused one.  The refusal read
        # *a merchant rule says where that merchant's SPENDING goes (R-GI), so
        # no rule can mean "record this deposit" (bank_import:R-GW)* -- true
        # while the answer set had four members, and ruling **R-HT(a)** added a
        # fifth that says what a DEPOSIT from a signature IS.  It is the same
        # act class R-GH consents to: it CREATES a row from a new bank line and
        # modifies nothing the owner made by hand.
        #
        # **The MATCH refusal above is untouched and is the whole boundary
        # now.** R-HT(b)'s group rule names a ROW SET, which modifies rows the
        # owner made, so it applies only on their OK -- and that is exactly the
        # act that reaches ``accept_match``.  So the one arm this may never
        # grow is the one still refused.

    @property
    def item_count(self) -> int:
        """Return how many acts this batch asks for."""
        return len(self.matches) + len(self.creations) + len(self.incomes)


@dataclass(frozen=True)
class AppliedItem:
    """One act that landed, as the receipt names it.

    Attributes:
        line_ids: The bank lines the act explains.  **A CORRELATION key, not a
            label**: a ``bank_statement_lines.id`` is opaque to the owner, who
            never sees one anywhere on this screen, so rendering "Line 4711:"
            beside a sentence pointed at nothing.  What identifies the act on
            screen is :attr:`summary`, which names its figure and its day.
            Carried because a caller -- and these tests -- must be able to say
            WHICH submitted item an outcome belongs to.  Named by adversarial
            design review 2026-08-19.
        summary: One sentence saying what it did, written by the door that did
            it.
        amount: What the BANK moved on the lines this act explains, signed on
            the bank's own convention -- negative for money leaving.  **ONE
            convention across both item kinds, and stating it is the whole
            point of the field**: a match's own figure is already the bank's
            (:attr:`~._accept.AcceptedMatch.amount`) and a creation's is the
            PURCHASE's, which is positive because a purchase is an expense, so
            a field that took each door's native sign would total two
            directions at once.  Added at plan step ``bank_import:X-ge``,
            because a receipt for acts nobody pressed has to name money and
            not only a count -- ruling **R-GD(a)**'s rule one door over: a
            consent naming a count and no figure is a consent to an amount
            nobody stated.
    """

    line_ids: "tuple[int, ...]"
    summary: str
    amount: Decimal


@dataclass(frozen=True)
class RefusedItem:
    """One act that was refused, as the receipt names it.

    **The sentence is the SERVICE's, verbatim.**  Every refusal in this package
    is written for the person who submitted the form and ends by saying nothing
    was changed; re-wording them here would put a second voice on a money
    screen, and summarising them would lose the figures that make one
    actionable -- the payroll gap names its own difference to the cent.

    Attributes:
        line_ids: The bank lines the refused act named, as a correlation key
            for the reason :class:`AppliedItem` gives.
        reason: The service's own sentence.  It names the act's own figures --
            which is what makes a refusal actionable, and why it is quoted
            rather than summarised.
    """

    line_ids: "tuple[int, ...]"
    reason: str


@dataclass(frozen=True)
class BatchOutcome:  # pylint: disable=too-many-instance-attributes
    """What a whole reviewed pass did.

    Pylint: too-many-instance-attributes (12/7) -- **twelve because a pass
    receipt has twelve things to say.**  ``refunded_count`` is the newest
    (ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b-3``) and it
    is here for exactly ``deposited_count``'s reason one sentence down: a
    merchant credit a rule files as a NEGATIVE purchase reported through
    ``recorded_count``, whose caption says *as a purchase your records did not
    have, dated the day your bank took it* -- which the bank did not do.
    ``deposited_count`` (ruling **bank_import:R-GW**) is here for
    ``repriced_count``'s reason: without
    it a pass that recorded a deposit reports through ``recorded_count``,
    whose caption says *as a purchase*, or through nothing at all.
    ``repriced_count`` is what stopped this
    panel rendering *"Nothing moved."* over a rewritten figure (2026-08-22),
    and dropping a count to satisfy a limit is how that sentence came to be
    false.  The last two are the residual pair, which names money this pass
    RECORDED that the app did not hold at all -- the one effect here that no
    other field can be read as covering.
    :class:`~._accept.AcceptedMatch` carries the same disable for the same
    reason.  *(This paragraph called ``repriced_count`` "the eighth" until
    2026-08-23; it is the sixth field declared.  An ordinal nobody can check
    against the list beside it is exactly the kind of claim this codebase
    keeps measuring wrong, so it is gone rather than corrected.)*

    Attributes:
        applied: The acts that landed, in the order they were applied.
        refused: The acts that were refused, in the same order.  **A refusal is
            an ordinary outcome here, not an error**: the ruled policy is that
            one bad item cannot cost the others, so a batch reporting refusals
            has still done everything it could.
        settled_count: How many rows the pass marked as having happened.
        corrected_count: How many settled rows had a day moved onto the bank's.
        repriced_count: How many rows had their FIGURE moved onto the bank's
            (:attr:`~._accept.AcceptedMatch.repriced_count`).  **Without it
            :attr:`moved_nothing` was FALSE rather than merely quiet**: a
            repricing whose row already carried the bank's day reports
            ``unchanged`` on every day count, so a pass that rewrote what a
            payment cost rendered *"Nothing moved."*  Found by adversarial
            design review 2026-08-22.
        redated_count: How many purchases had their PURCHASE day corrected
            (ruling **R-FW**).
        recorded_count: How many bank lines became a CHARGE the app did not
            have -- a purchase the bank took money for.  **Charges only since
            plan step ``bank_import:X-gj-2b-3``**; see :attr:`refunded_count`.
        refunded_count: How many bank lines became a REFUND against a budget
            line, lowering what that line has cost (ruling
            **bank_import:R-II**).  Its own count for the reason
            :attr:`deposited_count` is its own: both arms go through the same
            door as a charge does, and one caption cannot be true of all
            three.
        envelopes_created: How many budget lines the pass created to hold one,
            across BOTH directions.  **It has its own receipt line rather than
            a clause on the charge one** (plan step ``bank_import:X-gj-2b-3``):
            a refund can mint an envelope too -- a rule naming *a new envelope*
            for the merchant does it, budgeting `$0.00` and recording the
            refund -- so hanging the number off the charge sentence would
            report an envelope this pass created under a count that does not
            contain the line it holds.
        deposited_count: How many bank lines of money COMING IN became an
            uncategorized income row (ruling **bank_import:R-GW**).  **Its own count and
            not folded into** :attr:`recorded_count`, whose sentence on the
            receipt is *recorded as a purchase your records did not have* --
            false of a deposit.  **A count whose caption is false of half its
            members** is this arc's recurring defect, and the instances are
            NAMED rather than counted (plan step ``bank_import:X-gj-2b-3``):
            ``repriced_count`` split out 2026-08-22, ``deposited_count`` at
            ruling **R-GW**, ``residual_count`` at **R-FN**, and
            :attr:`refunded_count` at **R-II**.  Three separate docstrings
            carried a running tally of these, all reading *three times* or
            *twice*, and none was incremented when the fourth landed -- which
            is what a counter written as prose does.  **No
            TOTAL beside it, unlike the residual pair**, and the asymmetry is
            the netting: a residual is signed either way, so seven at
            `+$0.05` against one at `-$0.35` net to a figure that says
            nothing, while every deposit is POSITIVE by the door's own refusal
            and the itemisation names each one's figure.
        residual_count: How many matched GROUPS had their difference recorded
            as an ordinary uncategorized row (plan step
            ``bank_import:X-f6d-4``, ruling **R-FN**).
        residual_total: What those differences come to, signed and netted.
            **A figure beside the count rather than instead of it**, because
            the two answer different questions and the netting is why: seven
            payroll deposits at `+$0.05` and one refund at `-$0.35` net to
            `$0.00`, and a receipt saying "8 differences recorded" over a
            total of nothing would be true and useless.  The count says how
            many rows the owner now has to categorise; the total says how much
            money reached the Uncategorized bucket.
    """

    applied: "tuple[AppliedItem, ...]"
    refused: "tuple[RefusedItem, ...]"
    settled_count: int
    corrected_count: int
    redated_count: int
    repriced_count: int
    recorded_count: int
    refunded_count: int
    envelopes_created: int
    deposited_count: int
    residual_count: int
    residual_total: Decimal

    @classmethod
    def nothing(cls) -> "BatchOutcome":
        """Return the outcome of a pass that performed no act at all.

        **Not the same as a pass that was never run**, which is why it is a
        value rather than a ``None`` its callers branch on: a rule pass over an
        import that recorded no fresh line HAS run and found nothing to do, and
        an import whose rules could not be consulted has not
        (:class:`~._filing.RuleFiling`).  Both hold one of these; only the
        second carries a reason beside it.
        """
        return cls(
            applied=(), refused=(),
            settled_count=0, corrected_count=0, redated_count=0,
            repriced_count=0, recorded_count=0, refunded_count=0,
            envelopes_created=0,
            deposited_count=0,
            residual_count=0, residual_total=Decimal("0.00"),
        )

    @property
    def applied_count(self) -> int:
        """Return how many acts landed."""
        return len(self.applied)

    @property
    def refused_count(self) -> int:
        """Return how many acts were refused."""
        return len(self.refused)

    @property
    def moved_nothing(self) -> bool:
        """Return whether the pass changed no record at all.

        The screen's own question.  An applied item can still move nothing --
        a match that only confirms the day the app already held changes no
        column -- so counting applied items would claim work that did not
        happen, which is the distinction
        :class:`~._accept.AcceptedMatch` draws for a single act.
        """
        return not (
            self.settled_count
            or self.corrected_count
            or self.redated_count
            or self.repriced_count
            or self.residual_count
            or self.recorded_count
            # **Recording a REFUND moves money too** (ruling
            # **bank_import:R-II**, plan step ``bank_import:X-gj-2b-3``), and
            # it is named here for the reason the sentence below names
            # ``deposited_count``: this test has to name EVERY effect, and a
            # pass whose only act was recording a merchant credit would
            # otherwise render *"Nothing moved."* over a purchase it had just
            # created and an envelope it may have minted to hold it.
            or self.refunded_count
            # **Recording a deposit MOVES MONEY** (ruling **bank_import:R-GW**), so it
            # belongs here for the reason ``repriced_count`` was added: a pass
            # whose only act was one would otherwise render *"Nothing moved."*
            # over a row it had just created and settled.  Every arm of this
            # test has to name every effect, which is why the class is stated
            # rather than left to whoever adds the next count.
            or self.deposited_count
        )


def _match_summary(accepted) -> str:
    """Return the sentence describing what one accepted match did.

    **It names the three effects separately**, because they are different acts
    with different consequences: settling a row books money the projection was
    still holding forward, correcting a settle day moves money already booked
    from one day to another, and correcting a PURCHASE day moves no money at
    all but rewrites when the owner says they bought something.  A single
    "2 rows updated" would hide which -- and the third was folded into the
    first until adversarial review 2026-08-18 measured that the case it was
    built for (an unsettled purchase, re-dated by up to 59 days) reported only
    "marked 1 row(s) as having happened".

    Args:
        accepted: The :class:`~._accept.AcceptedMatch`.

    Returns:
        The sentence.
    """
    did = []
    if accepted.settled_count:
        did.append(
            f"marked {accepted.settled_count} row(s) as having happened"
        )
    if accepted.corrected_count:
        did.append(
            f"moved {accepted.corrected_count} row(s) onto the bank's day"
        )
    if accepted.redated_count:
        did.append(
            f"corrected the purchase date on {accepted.redated_count} row(s)"
        )
    # **The AMOUNT, and it is named LAST because it is the one thing here that
    # changes what money was SPENT** rather than when it moved.  Without it a
    # repricing on a row already carrying the bank's day fell through to
    # "confirmed what you already had" -- a sentence that was not merely silent
    # but false.
    if accepted.repriced_count:
        did.append(
            f"corrected the amount on {accepted.repriced_count} row(s)"
        )
    # **After it, because it is the only clause naming a row that did not
    # exist before this act** (plan step bank_import:X-f6d-4).  It says
    # "recorded" rather than "corrected" for that reason: nothing of the
    # owner's was changed to produce it.
    if accepted.residual is not None:
        did.append(
            f"recorded the {accepted.residual:+,.2f} difference as a row "
            f"with no category"
        )
    what = " and ".join(did) if did else "confirmed what you already had"
    return (
        f"Matched {accepted.line_count} statement line(s) worth "
        f"{accepted.amount:+,.2f} on {accepted.posts_on}: {what}."
    )


def _created_summary(recorded) -> str:
    """Return the sentence describing what recording one line did.

    **It names the container and whether it was created**, because those are
    different acts: filing a purchase under an envelope the owner already
    budgeted changes what that envelope RECORDS as its cost, while creating one
    adds a budget line to a period that did not have it.

    **It names both days only when they differ.**  A purchase carries the day
    it was MADE beside the day the bank TOOK it (ruling **R-FW**), and on 179
    of the developer's 361 lines the source states no separate made-day at all
    -- so printing "made on" unconditionally would report the clearing day as a
    swipe day on half of every statement, which is the exact substitution R-FW
    rejected.

    **The FIGURE is negated once; the VERB reads the field the door stated**
    (ruling **R-II**).  ``CreatedPurchase.amount`` is the purchase's own signed
    figure, so a refund is negative and the statement states the same movement
    the other way round -- one negation, so this sentence and the
    ``AppliedItem.amount`` beside it cannot disagree about which way the money
    went.  **The verb asks** :attr:`~._creations.CreatedPurchase.records_a_refund`
    **rather than the sign of that figure** (plan step
    ``bank_import:X-gj-2b-3``): that field exists so nothing downstream
    re-derives a direction, and this function was the first consumer written
    after it and re-derived one anyway.  The two agree by construction today --
    ``_born_purchase`` sets both from one line -- which is exactly why a second
    spelling here would be invisible until it was not.

    Args:
        recorded: The :class:`~._create.CreatedPurchase`.

    Returns:
        The sentence.
    """
    where = (
        f"a new envelope, {recorded.envelope_label}"
        if recorded.envelope_created
        else recorded.envelope_label
    )
    made = (
        f", made {recorded.made_on}" if recorded.made_on != recorded.posts_on
        else ""
    )
    # **The BANK's convention** (plan step ``bank_import:X-gj-2b``, ruling
    # **R-II**).  A refund is a NEGATIVE purchase, and this sentence printed
    # ``$-42.00 your bank took`` -- the wrong sign AND the wrong direction, in
    # the one notice the owner gets for money a rule moved without a press
    # (**R-GH**).  It also contradicted ``AppliedItem.amount`` on the same
    # item, which negates onto the bank's convention; the negation is done
    # ONCE here so the two cannot disagree.
    on_the_statement = -recorded.amount
    # **The VERB is the DOOR's answer, not this figure's sign** (plan step
    # ``bank_import:X-gj-2b-3``): ``records_a_refund`` is set by
    # ``_born_purchase`` from ``_rules.is_inflow``, which is this package's one
    # statement of the bank's sign convention.
    took_or_gave = "gave back" if recorded.records_a_refund else "took"
    return (
        f"Recorded ${abs(on_the_statement):,.2f} your bank {took_or_gave} on "
        f"{recorded.posts_on}{made} as a purchase in {where}."
    )


def _income_summary(recorded) -> str:
    """Return the sentence describing what recording one deposit did.

    **It names no container and no second day, because the row has neither**
    (ruling **bank_import:R-GW**).  ``_created_summary`` beside it names both because a
    purchase is filed against something that reserves for it and carries a
    budget clock of its own; an income row IS the movement, so the day the bank
    credited it is the only day there is.

    **It says the row has no category**, which is the one thing about it the
    owner has to act on later: the money is recorded correctly and filed
    nowhere, and a receipt that did not say so would leave the owner to find
    out from the grid.

    Args:
        recorded: The :class:`~._creations.RecordedIncome`.

    Returns:
        The sentence.
    """
    return (
        f"Recorded ${recorded.amount:,.2f} your bank paid in on "
        f"{recorded.posts_on} as {recorded.label}, with no category yet."
    )


@dataclass
class _Tally:  # pylint: disable=too-many-instance-attributes
    """The running receipt one pass builds.

    Pylint: too-many-instance-attributes (12/7) -- it accumulates exactly
    the counters :class:`BatchOutcome` publishes, so it carries that
    class's disable for that class's reason.

    Mutable and private, because it IS the loop's accumulator; what leaves this
    module is the frozen :class:`BatchOutcome` built from it.
    """

    applied: list
    refused: list
    settled: int = 0
    corrected: int = 0
    redated: int = 0
    repriced: int = 0
    recorded: int = 0
    refunded: int = 0
    envelopes: int = 0
    deposited: int = 0
    residuals: int = 0
    residual_total: Decimal = Decimal("0.00")


def _run(tally: _Tally, line_ids: "tuple[int, ...]", act) -> object:
    """Run one act inside its own SAVEPOINT and record what happened.

    **The savepoint is what makes the ruled failure policy true rather than
    reassuring.**  A refused act may already have staged rows -- ``_create``
    creates a purchase before the match that names it is validated, and a
    settle verb can refuse mid-way through a group -- so "a refused item leaves
    nothing behind" needs the partial work undone without touching the items
    that landed before it.

    **``ValidationError`` and ``NotFoundError`` are caught, and nothing else.**
    Both are this project's DESIGNED refusals -- a sentence written for the
    person who submitted the form -- and they are SIBLINGS rather than one
    deriving from the other (``app/exceptions.py``), which is why naming only
    the first left a hole.  ``entry_service.update_entry`` and ``create_entry``
    raise ``NotFoundError`` for a row that has gone, so a row hard-deleted
    between this pass's derivation and this item's write took the whole request
    down as a 500 -- where the same event is a designed stale-page refusal
    everywhere else on this screen.  Found by adversarial financial review
    2026-08-19.

    Anything else propagates and fails the whole request, which is the right
    answer for a ``PostingError`` (a broken ledger invariant is a fact about
    the account, not about this item) and for a database error.

    Args:
        tally: The running receipt.
        line_ids: The bank lines this act names, for the receipt.
        act: The service call, taking no arguments.

    Returns:
        Whatever *act* returned, or ``None`` when it was refused.
    """
    savepoint = db.session.begin_nested()
    try:
        result = act()
        # **Inside this item's savepoint, and that is the point.**  Autoflush
        # would otherwise emit THIS item's INSERTs while the NEXT item's first
        # query runs -- inside the next item's savepoint -- so refusing that
        # one would roll back work this one had landed.  An earlier comment
        # here claimed the flush keeps an ``IntegrityError`` inside the
        # savepoint, which it does not: only a designed refusal is caught, so
        # an integrity error fails the whole request either way.  Named by
        # adversarial test-quality review 2026-08-19.
        #
        # It is also what makes the next item's refusals see this one: they
        # read what is already matched, and whether a row names an envelope
        # whose purchase this act just claimed.
        db.session.flush()
    except (ValidationError, NotFoundError) as exc:
        savepoint.rollback()
        tally.refused.append(RefusedItem(line_ids=line_ids, reason=str(exc)))
        return None
    savepoint.commit()
    return result


def apply_reviewed(batch: ReviewedBatch, scope: ReviewScope) -> BatchOutcome:
    """Apply every act the owner ticked, and report each one.

    Does NOT commit -- the route owns the session boundary, so a request that
    fails outside a designed refusal writes nothing at all.

    **It does not LOG the pass either**, and that is the same boundary: an
    event asserting "a reviewed pass was applied" must not sit in the log when
    the transaction that would have applied it failed, so the route emits it
    after its commit -- exactly as ``statements.record_statement``'s own
    business event is emitted by its route rather than by its service.

    Args:
        batch: What the owner ticked.
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).
            **The ROUTE builds it, exactly as only a route builds a
            ``BalanceContext``**, and this door takes it like every door
            beneath it.  A first draft built its own, which is the same shape
            one tier up as the per-act derivation this step exists to remove:
            the route needs that scope too -- to render the refusal a rolled-
            back pass leaves behind -- and a door that derives privately forces
            the caller to derive again.

    Returns:
        The :class:`BatchOutcome`.

    Raises:
        PostingError: From a ledger reconcile, on a broken invariant.  Fails
            the whole request loud rather than being reported as one item's
            refusal.
    """
    tally = _Tally(applied=[], refused=[])
    # **One registry per REQUEST**, which is what makes a sweep mint one
    # envelope per answer per pay period rather than one per line (finding
    # **N-327**).  Built HERE rather than inside the create door for the reason
    # the scope is built by the route: a door that made its own would be a door
    # that converges with nothing, one line at a time, which is the defect.
    minted = MintedEnvelopes.none_yet()
    # **One derivation per DOOR, and that is the precise claim** -- an
    # adversarial review 2026-08-24 measured a first version of this comment
    # saying "per REQUEST", which is false: this route re-renders after the
    # write, and ``_leftovers`` builds its own bars for the screen, so
    # ``merchant_rules`` is read once here and once there.  That is the
    # shape ``state_rules`` already argues for and ships -- a door reads for
    # ITSELF across a write boundary, because sharing one read across it would
    # rest on an enumeration of what cannot have changed.
    #
    # What this DOES remove is the per-ACT read: ruling **R-GJ** bars a
    # merchant from becoming a purchase, nothing inside a batch can restate a
    # rule, and a door that re-read the table per act would ask it 90 times
    # for the developer's own statement.  Built HERE rather than on the
    # :class:`~._scope.ReviewScope` because the rule-stating route derives
    # its scope BEFORE its write, so a scope-carried answer would be the one
    # that pass had just replaced.  It is built unconditionally, including for
    # a pass carrying no creations at all: two indexed reads, measured at
    # 0.14 ms over the developer's 378 lines, against a value whose only
    # cheaper form would be one that means *nothing is barred*.
    #
    # **The RULE VIEW is read here too, and the two SHARE that read** (plan
    # step ``bank_import:X-gj-2a``).  ``CreationBars.build`` already accepts
    # the rules rather than re-reading them, which is how ``_leftovers``
    # builds both from one read; doing the same here means this door still
    # asks ``merchant_rules`` exactly ONCE, as the paragraph above claims,
    # while the income arm below gains the answer it needs.
    answers = MerchantAnswers.build(scope.owner_id, scope.account_id)
    view = answers.view

    for submission in batch.matches:
        line_ids = tuple(sorted(submission.line_ids))
        accepted = _run(
            tally, line_ids, lambda s=submission: accept_match(s, scope),
        )
        if accepted is None:
            continue
        tally.settled += accepted.settled_count
        tally.corrected += accepted.corrected_count
        tally.redated += accepted.redated_count
        tally.repriced += accepted.repriced_count
        if accepted.residual is not None:
            tally.residuals += 1
            tally.residual_total += accepted.residual
        tally.applied.append(AppliedItem(
            line_ids=line_ids, summary=_match_summary(accepted),
            # Already the BANK's own signed figure over the lines this act
            # names, which is this field's stated convention.
            amount=accepted.amount,
        ))

    for creation in batch.creations:
        line_ids = (creation.line_id,)
        recorded = _run(
            tally, line_ids,
            lambda c=creation: create_purchase_from_line(
                c, scope, minted, answers,
                # **The PASS's consent, not the item's** (ruling **R-GT**,
                # plan step ``bank_import:X-ge``).  Which rule fired is
                # derivable from the matched line; that a rule fired at all is
                # not, and it is a fact about how this whole batch was
                # assembled rather than about any one line in it.
                applied_by_rule=batch.consent.applied_by_rule,
            ),
        )
        if recorded is None:
            continue
        # **AFTER the act returned**, never inside the door that creates: an
        # item refused by ``create_entry`` rolls its whole SAVEPOINT back, and
        # a registry written one line above that refusal hands the NEXT line an
        # id the rollback has already taken.  Measured -- the sweep died on
        # ``NoneType`` -- which is why the remembering lives out here.
        if recorded.envelope_created:
            minted.remember(creation.new_envelope, recorded)
        # **Counted by DIRECTION, off the field the door stated** (ruling
        # **bank_import:R-II**, plan step ``bank_import:X-gj-2b-3``).  One
        # count reported both, and the receipt's caption for it -- *recorded as
        # a purchase your records did not have, dated the day your bank took
        # it* -- is false of a refund in both halves.  The direction is
        # ``CreatedPurchase.records_a_refund``, which the create door resolved
        # through ``_rules.is_inflow`` while it held the line, so nothing here
        # tests a sign.
        if recorded.records_a_refund:
            tally.refunded += 1
        else:
            tally.recorded += 1
        tally.envelopes += 1 if recorded.envelope_created else 0
        tally.applied.append(AppliedItem(
            line_ids=line_ids, summary=_created_summary(recorded),
            # **NEGATED, onto the bank's convention.**
            # ``CreatedPurchase.amount`` is the purchase's own figure and the
            # bank states the same movement with the opposite sign, so the
            # receipt negates it back.  **Sign-general by construction**: the
            # negation is what the bank's convention is, not an assumption that
            # the purchase is positive, so a refund recorded at ``-28.29``
            # reports the ``+28.29`` the statement shows (ruling
            # **bank_import:R-II**).  Refunds DO reach this door since plan
            # step ``bank_import:X-gj-2b-2``, so the general case is the
            # ordinary one rather than a future one.
            amount=-recorded.amount,
        ))

    for income in batch.incomes:
        line_ids = (income.line_id,)
        deposited = _run(
            tally, line_ids,
            lambda i=income: record_income_from_line(
                i, scope, view,
                # **The PASS's consent, not the item's** (ruling **R-GT**),
                # which is the same threading the creation arm above does: the
                # act records whether a RULE performed it, and only the batch
                # knows that.
                applied_by_rule=batch.consent.applied_by_rule,
            ),
        )
        if deposited is None:
            continue
        tally.deposited += 1
        tally.applied.append(AppliedItem(
            line_ids=line_ids, summary=_income_summary(deposited),
            # **Already the BANK's own direction.**  An income row's cash
            # effect is POSITIVE and so is the line it was built from
            # (``_income._load_line`` refuses anything else by name), so unlike
            # the purchase arm above there is no sign to flip -- and flipping
            # one here would report a deposit as a withdrawal.
            amount=deposited.amount,
        ))

    outcome = BatchOutcome(
        applied=tuple(tally.applied),
        refused=tuple(tally.refused),
        settled_count=tally.settled,
        corrected_count=tally.corrected,
        redated_count=tally.redated,
        repriced_count=tally.repriced,
        recorded_count=tally.recorded,
        refunded_count=tally.refunded,
        envelopes_created=tally.envelopes,
        deposited_count=tally.deposited,
        residual_count=tally.residuals,
        residual_total=tally.residual_total,
    )
    return outcome
