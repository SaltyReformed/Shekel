"""What a reviewed pass DID, as the value it hands back.

Plan step ``bank_import:X-gt``, finding **BI-491**: a PURE MOVE out of
:mod:`._batch`, which stood at 998 of pylint's 1000-line ceiling after plan
step ``bank_import:X-gi-5``, with ``balance:X-bn`` still to put an advisory
lock into it.  The cut was presented as a fork and ruled by the developer on
2026-09-12 (ruling **bank_import:R-BI7**, in **R-PC71**'s shape, the
placement his as **R-PC60** and **R-PC69** were): the receipt WHOLE leaves,
and the pass stays.

**The seam is the one** :mod:`._batch`'s **own first line draws** -- *apply
everything the owner reviewed in ONE pass, and say what each item did* -- so
this module is the second clause.  What it holds:

* :class:`AppliedItem` and :class:`RefusedItem` -- one act each, as the
  receipt names it, the first carrying the bank's own signed figure;
* :class:`BatchOutcome` -- the whole pass, fourteen fields: both lists and
  every count, each count's docstring recording why it exists rather than
  being folded into a neighbour whose caption would be false of it;
* :class:`Tally` -- the running accumulator :mod:`._batch`'s four arms and
  its :func:`~._batch._run` write, and :func:`~._batch.apply_reviewed`
  freezes into the outcome, last.

Nothing in the pass reads any of these back: the arms append and increment,
``_run`` appends each refusal, and the door constructs the frozen value once
every arm has run.  That is what makes the split a placement rather than a
design change -- the four moved bodies compute exactly what they did in
:mod:`._batch`, ``$0.00``.

**``Tally`` is PUBLIC and was ``_Tally`` before the move**, for the reason
:mod:`._receipt_sentences` gives for its three: a name a sibling module
constructs is part of this module's surface.  Nothing else changed on the way
across except the references that crossed the new boundary, which a
byte-pure move cannot keep true (finding **BI-484**) and which the move's own
commit lists.

**The dependency runs ONE WAY**: the package's ``__init__``, :mod:`._batch`
and :mod:`._filing` import this module, and it imports nothing from the
package -- only ``dataclass`` and ``Decimal``.

Services-boundary discipline (``CLAUDE.md`` Architecture): frozen dataclasses
out, no Flask import, no query -- every figure on these values arrived on
what a door returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


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

    Pylint: too-many-instance-attributes (14/7) -- **fourteen because a pass
    receipt has fourteen things to say.**  ``skipped_count`` and
    ``already_skipped_count`` are the newest
    (ruling **bank_import:R-JG**, plan step ``bank_import:X-gj-4b``) and it is
    here for the reason every count below it is: a skip records a decision and
    no figure, so folding it into any existing count would put it under a
    caption that is false of it -- and leaving it out altogether would render
    *"Nothing moved."* over a pass that emptied four cards out of the inbox.
    ``refunded_count`` (ruling
    **bank_import:R-II**, plan step ``bank_import:X-gj-2b-3``) was the newest
    until then, and it is here for exactly ``deposited_count``'s reason one
    sentence down: a
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
    false.  ``residual_count`` and ``residual_total`` are the residual pair,
    which names money this pass RECORDED that the app did not hold at all -- the one effect here
    that no
    other field can be read as covering.
    :class:`~._accept.AcceptedMatch` carries the same disable for the same
    reason.  *(This paragraph called ``repriced_count`` "the eighth" until
    2026-08-23; it is declared in the field block below, where an ordinal can
    be READ OFF rather than asserted.  An ordinal nobody can check against the
    list beside it is exactly the kind of claim this codebase keeps measuring
    wrong, so it is gone rather than corrected.)*

    Attributes:
        applied: The acts that landed, in the order they were applied.
        refused: The acts that were refused, in the same order.  **A refusal is
            an ordinary outcome here, not an error**: the ruled policy is that
            one bad item cannot cost the others, so a batch reporting refusals
            has still done everything it could.
        settled_count: How many rows the pass marked as having happened.
        corrected_count: How many settled rows had a day moved onto the bank's.
        redated_count: How many purchases had their PURCHASE day corrected
            (ruling **R-FW**).
        repriced_count: How many rows had their FIGURE moved onto the bank's
            (:attr:`~._accept.AcceptedMatch.repriced_count`).  **Without it
            :attr:`moved_nothing` was FALSE rather than merely quiet**: a
            repricing whose row already carried the bank's day reports
            ``unchanged`` on every day count, so a pass that rewrote what a
            payment cost rendered *"Nothing moved."*  Found by adversarial
            design review 2026-08-22.
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
        skipped_count: How many bank lines the owner recorded as explained by
            NOTHING (ruling **bank_import:R-JG**).  **It counts what was
            WRITTEN and not what was pressed**: a repeat press finds the
            decision already standing and writes nothing
            (:attr:`~._skipping.SkippedLine.was_already_skipped`), so it is an
            applied item with its own sentence and it does NOT increment this
            -- a count that included it would claim a record changed when none
            did, and the reader that would then state it falsely is the
            RECEIPT's own caption (*recorded as explained by nothing*) and
            the audit field beside it.  **Not** :attr:`moved_nothing`, which
            ORs both counts and would answer identically either way; that
            predicate's reason to exist lives on
            :attr:`already_skipped_count`, where it is true.
        already_skipped_count: How many skips this pass found ALREADY
            standing and wrote nothing for
            (:attr:`~._skipping.SkippedLine.was_already_skipped`).  **Its own
            count beside :attr:`skipped_count` rather than folded into it**,
            because the two answer different questions and one integer cannot
            answer both: that count says how many decisions were RECORDED, so
            counting a repeat would claim a record changed when none did,
            while :attr:`moved_nothing` has to know the pass has something to
            report or it renders *"Nothing moved"* over an act that confirmed
            no day.  Reachable from a stale or duplicated tab, and from
            two submits of one page.  *An earlier draft said a stale tab was
            the ONLY place a repeat comes from*, which is the
            one-writer-from-wrong shape.  What IS exclusive is that no single
            PRESS can produce one: ``reconcile_payload`` dedupes on
            ``set(form.getlist("ok"))``, so one body yields one item a line.  Named by
            adversarial review 2026-09-04.
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
    skipped_count: int
    already_skipped_count: int

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
            skipped_count=0, already_skipped_count=0,
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
        """Return whether every act that LANDED did nothing but confirm.

        **It is NOT "changed no record at all", and that sentence stood here
        until plan step ``bank_import:X-gj-4b``** (adversarial review
        2026-09-04).  A repeat press changes no record -- that is exactly why
        it increments ``already_skipped_count`` and not ``skipped_count`` --
        and this answers ``False`` for it, because the receipt still owes the
        owner a sentence about the press they made.  The question this asks is
        whether the alternative arm's wording (*everything that was applied
        confirmed a day you already had*) would be TRUE of the pass.

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
            # **A skip changes a RECORD, which is what this predicate asks**
            # -- and it is not a money effect (plan step
            # ``bank_import:X-gj-4b``).  ``budget.statement_line_skips`` holds
            # no figure and a skip closes no difference between the books and
            # the bank, so nothing MOVED; what changed is that the skipped
            # lines left the inbox and the Skipped tab now holds them.  Without this arm
            # a skip-only pass rendered *"Nothing moved.  Everything that was
            # applied confirmed a day you already had"*, whose second sentence
            # is false of an act that confirms no day at all.
            or self.skipped_count
            # **A REPEAT wrote nothing and still has something to report**,
            # which is why it is a second counter rather than a second reason
            # to increment the first (adversarial review 2026-09-04).
            # ``skipped_count`` answers *how many decisions were recorded* and
            # must not count a press that recorded none; this predicate asks
            # *has the pass anything to say beyond confirming days*, and a
            # repeat-only pass does -- without this arm it rendered *"Nothing
            # moved.  Everything that was applied confirmed a day you already
            # had"* over an act that confirms no day at all, which is the very
            # sentence the arm above was added to stop printing.
            or self.already_skipped_count
        )


@dataclass
class Tally:  # pylint: disable=too-many-instance-attributes
    """The running receipt one pass builds.

    Pylint: too-many-instance-attributes (14/7) -- it accumulates exactly
    the counters :class:`BatchOutcome` publishes, so it carries that
    class's disable for that class's reason.

    Mutable, because it IS the loop's accumulator (:mod:`._batch`'s arms and its
    ``_run`` write it); what leaves that pass is the frozen :class:`BatchOutcome`
    built from it.
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
    skipped: int = 0
    already_skipped: int = 0
