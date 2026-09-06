"""What standing answer would say *do that again*, for a pass that just ran.

Plan step ``bank_import:X-gj-1b``, ruling **bank_import:R-IB** (developer,
2026-08-30).  The Reconcile card carried an *always, for this merchant*
checkbox until that ruling, and the offer moved here -- once per MERCHANT, on
the RECEIPT, about what the door actually APPLIED.

**The cause was a grain mismatch, and two defects fell out of it.**  A standing
rule is ONE fact per merchant; the card rendered it once per LINE.  Measured
2026-08-30 on a restored production clone of the developer's own account: 86
creatable lines over 21 merchants, so the page asked one question **86 times**
-- Amazon **26**, Walmart **13**, Food Lion **12**, ten merchants over-asked.

* the rule was read off what was **OK'd**, and computed BEFORE the money door
  ran, so a per-item refusal rolled back inside its savepoint while the rule
  was written anyway -- auto-filing that merchant on the NEXT import with no
  press.  Built from :attr:`~._batch.BatchOutcome.applied` here, a rule for a
  refused creation has no form to take;
* N controls for one fact can state N different answers.  One offer per
  merchant leaves a contradiction no form to take either.  **The measured
  contradiction rate is ZERO** -- a rule keys on ``template_id``, so all 13
  Walmart lines and all 26 Amazon lines collapse to one answer each and the
  app's own suggestions never disagree -- so this removes the SHAPE rather
  than fixing a live bug, which is the distinction worth keeping.

**It offers and never states.**  Nothing here writes: the offer is rendered,
the owner presses, and the press goes to the door the review queue and the
register already post to
(:func:`~app.routes.accounts._statement_rules.record_submitted_rules`).  One
grader, one writer, three surfaces.

**What it offers is a :class:`~._stating.RuleSubmission` and not a destination
id**, so the rule an offer describes and the rule the door would record are one
derivation.  :func:`~._stating.rule_naming` is what turns the budget line the
owner chose into the period-independent answer a rule can hold -- a template,
or an envelope named by name and category -- and asking it here is what keeps
ruling **R-GI**'s substance after its surface moved: the answer is still read
back off the destination and never typed.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no query, no clock read.  It READS and
never writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._creations import NEW_ENVELOPE, NewEnvelope
from ._stating import RuleSubmission, rule_creating, rule_naming

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from ._reads import ReviewSet
    from ._scope import ReviewScope


@dataclass(frozen=True)
class OfferedAnswer:
    """One place a merchant's purchases landed, as a rule the owner may state.

    Attributes:
        statement: The :class:`~._stating.RuleSubmission` pressing this would
            record.  **The service's own value and not a wire string**: the
            rule an offer describes and the rule the door records are one
            derivation through :func:`~._stating.rule_naming`, so a receipt
            cannot promise a budget line the door would not file into.
        label: What to CALL the destination in the offer's sentence -- a
            ``template_id`` is a surrogate and means nothing to a reader.

            **The budget line's own name, and never the period-labelled one
            the card showed.**  The two are different questions:
            :attr:`~._creations.PurchaseDestination.label` names the pay
            period so an owner choosing between two copies of one envelope can
            tell them apart, and a STANDING answer applies to every period, so
            borrowing that label would promise a rule that expires.
        count: How many of this merchant's purchases this pass filed here.
            **The number the sentence promises**, counted in the service for
            the reason :func:`~._queue._sweeps_for` counts there: a caption may
            not promise a figure a template computed.
        blocked: Why the rule door would REFUSE this answer, or ``None`` where
            it would take it.

            **The offer set and the RULE door's accepted set are not the same
            set, and an adversarial review reproduced both ways they differ.**
            A destination is offered by
            :func:`~._candidates.destinations_for`, which never asks whether a
            template is still active; :func:`~._stating.state_rules` validates
            against :func:`~._rules.offerable_templates` and an ACTIVE
            category.  So a settled envelope from an archived template, and an
            ad-hoc envelope under an archived category, are both real
            destinations this pass can file into and neither can be made
            standing.  Rendering them as pressable would be the *chooser whose
            submission can never succeed* shape this package's own docstrings
            say it has closed four times.
    """

    statement: RuleSubmission
    label: str
    count: int
    blocked: "str | None" = None


@dataclass(frozen=True)
class RuleOffer:
    """One merchant this pass filed spending for, and what it could stand for.

    Attributes:
        merchant_id: The merchant the offer answers for.
        merchant: Its name, for the sentence.
        answers: Where this pass put that merchant's purchases, one entry per
            DISTINCT standing answer, in the order the pass applied them.
            **Never empty**, because a merchant with nothing applied produces
            no offer at all.

    **More than one answer is the owner having split a merchant**, which is a
    real thing to do and not an error: two Amazon swipes may honestly belong in
    two envelopes.  What it means for the OFFER is that the app cannot know
    which one should stand, so the receipt asks instead of guessing -- and
    because the question is asked once, there is no form in which two answers
    are both submitted.
    """

    merchant_id: int
    merchant: str
    answers: "tuple[OfferedAnswer, ...]"

    @property
    def filed_count(self) -> int:
        """Return how many purchases this pass filed for this merchant."""
        return sum(answer.count for answer in self.answers)

    @property
    def offerable(self) -> "tuple[OfferedAnswer, ...]":
        """Return the answers this offer can actually put on the wire.

        **The wire pairs a NEW-ENVELOPE answer's name and category with the
        merchant, not with the option**, because
        :func:`~app.schemas.validation.merchant_rules.rule_payload` reads
        ``rule_name-<key>`` and ``rule_category-<key>`` beside ``rule-<key>``
        and the key has to BE the merchant -- that is what makes one answer
        per merchant structural rather than a rule a template remembers.  So a
        merchant this pass filed into two DIFFERENT new envelopes has two
        answers and one pair of name fields, and only one of them can travel.

        **Stated here rather than in Jinja**, because it decides which options
        a money-adjacent control offers, and a template picking them would be
        a second statement of the form's own pairing rule.

        Returns:
            Every TEMPLATE answer the rule door would accept, plus at most the
            FIRST such new-envelope one, in application order.  On the
            developer's own data this is the whole of :attr:`answers` for
            every merchant -- ten repeated merchants, every one resolving to a
            single answer -- so both narrowings are properties of the wire and
            of the door rather than things the owner meets.
        """
        kept, minted = [], False
        for answer in self.answers:
            if answer.blocked is not None:
                continue
            if answer.statement.template_id is not None:
                kept.append(answer)
            elif not minted:
                kept.append(answer)
                minted = True
        return tuple(kept)

    @property
    def unofferable(self) -> "tuple[OfferedAnswer, ...]":
        """Return the answers this offer must NAME but cannot offer.

        Two kinds: an answer the rule door would REFUSE
        (:attr:`OfferedAnswer.blocked`), and the second and later new-envelope
        answers, which the wire cannot carry because ``rule_name-<key>`` is
        keyed by merchant.  **Reported rather than dropped**: the receipt says
        what it filed and where, so an answer that cannot be made standing is
        still a thing the owner did, and silently omitting it would make the
        counts on the receipt disagree with the pass.  Each carries its own
        reason, so the sentence beside it can say which.
        """
        offerable = set(self.offerable)
        return tuple(
            answer for answer in self.answers if answer not in offerable
        )

    @property
    def is_split(self) -> bool:
        """Return whether this pass put the merchant in more than one place.

        The receipt's own question: one answer is offered as *always file it
        there*, and several have to be chosen between.
        """
        return len(self.answers) > 1


@dataclass(frozen=True)
class RuleDoorAccepts:
    """What :func:`~._stating.state_rules` would actually take.

    Plan step ``bank_import:X-gj-1b``.  **A value the ROUTE reads and hands
    in**, because this module answers no query -- and because the two sets
    below belong to the rule door rather than to the pass, so deriving them
    from :class:`~._scope.ReviewScope` would be inventing a second answer to a
    question that already has one.

    Attributes:
        template_ids: :func:`~._rules.offerable_templates`' keys -- the
            recurring definitions a rule on this account may name.
        category_ids: The owner's ACTIVE categories, which
            :func:`~._stating.state_rules` requires of a new-envelope answer.
    """

    template_ids: "frozenset[int]"
    category_ids: "frozenset[int]"

    def refusal_for(self, statement: RuleSubmission) -> "str | None":
        """Return why the rule door would refuse *statement*, or ``None``.

        Args:
            statement: The answer an offer would put on the wire.

        Returns:
            A short reason naming what is wrong, for the sentence beside the
            answer, or ``None`` where the door would take it.
        """
        if statement.template_id is not None:
            if statement.template_id not in self.template_ids:
                return (
                    "that recurring envelope is no longer offered on this "
                    "account, so it cannot be a standing answer"
                )
            return None
        if statement.category_id not in self.category_ids:
            return (
                "that category is archived, so a new envelope under it "
                "cannot be a standing answer"
            )
        return None


def rules_worth_offering(
    creations: "list[dict]",
    applied_line_ids: "frozenset[int]",
    review: "ReviewSet",
    scope: "ReviewScope",
    accepts: RuleDoorAccepts,
) -> "tuple[RuleOffer, ...]":
    """Return one offer per merchant whose purchases this pass RECORDED.

    Ruling **bank_import:R-IB**.

    **``applied_line_ids`` is the whole point of the signature.**  Reading the
    submission alone is what wrote a standing rule for a creation the door had
    refused: :func:`~._batch.apply_reviewed` runs each item in its own SAVEPOINT
    (ruling **R-FZ(a)**), so a refusal rolls that item back while the pass
    commits, and a rule derived from what was OK'd survives an act that did
    not.  Taking the ids the outcome reports makes the offer a function of what
    LANDED.

    **An INCOME states nothing** (ruling **bank_import:R-GW**): a merchant
    answer says where SPENDING goes, so ``submitted["incomes"]`` is not read
    and no inflow reaches this loop.  A creation whose line carries no merchant
    at all states nothing either -- there is nobody to answer for.

    **A destination the scope does not offer is skipped rather than refused.**
    The money door refuses that same submission on the same request, which is
    the one place that refusal belongs; an offer built beside it would report a
    second opinion about an act that did not happen.

    Args:
        creations: The creation items
            :class:`~app.schemas.validation.statements.StatementBatchSchema`
            loaded, each naming a ``line_id`` and the destination the card
            submitted.
        applied_line_ids: Every bank line the door actually explained, from
            :attr:`~._batch.AppliedItem.line_ids`.
        review: The pass the cards were drawn from, for each line's merchant.
        scope: The pass, whose ``destinations`` are the offer set a chosen id
            is resolved against.  **Resolved against the SCOPE's own set
            rather than queried for**: it is already derived, and a second read
            could answer differently from the one the screen was drawn from.
        accepts: What the rule door would take
            (:class:`RuleDoorAccepts`).  **A different set from the
            destinations**, and an adversarial review reproduced both ways
            they part: a settled envelope from an archived template, and an
            ad-hoc envelope under an archived category, are each a real place
            this pass can file into and neither can be made standing.

    Returns:
        One :class:`RuleOffer` per merchant, in the order the pass carries
        them, each holding its distinct answers in application order.  Empty
        where the pass filed no spending, which is every pass that only matched
        or only recorded income.
    """
    lines = {
        one.line.line_id: one.line for one in review.creatable
        if one.line.merchant_id is not None
    }
    names = {
        line.merchant_id: line.merchant_label for line in lines.values()
    }
    offered = {
        destination.transaction_id: destination
        for destination in scope.destinations
    }
    return _by_merchant(
        _stated(creations, applied_line_ids, lines, offered, accepts), names,
    )


def _stated(creations, applied_line_ids, lines, offered, accepts):
    """Return ``(merchant_id, statement, label)`` per creation that LANDED.

    Split out of :func:`rules_worth_offering` when that function crossed
    pylint's local-variable ceiling.  **Decomposed rather than wrapped**, which
    is this project's rule for a private helper over a limit: what came out is
    the per-item question -- *what standing answer would this one creation
    state* -- and what stays behind is the per-merchant tally.

    Args:
        creations: The creation items the schema loaded.
        applied_line_ids: Every bank line the door actually explained.
        lines: The pass's creatable lines by id, merchant-bearing only.
        offered: The pass's destinations by ``transaction_id``.
        accepts: What the rule door would take.

    Yields:
        One ``(merchant_id, RuleSubmission, label, blocked)`` per creation that
        landed and resolves, in the pass's own order.  ``blocked`` is the rule
        door's refusal for that answer or ``None``, computed HERE because this
        is where *accepts* is in scope.
    """
    for item in creations:
        line = lines.get(item["line_id"])
        if line is None or item["line_id"] not in applied_line_ids:
            continue
        if item["destination"] == NEW_ENVELOPE:
            minted = rule_creating(
                line.merchant_id,
                NewEnvelope(
                    name=item["envelope_name"],
                    category_id=item["category_id"],
                ),
            )
            yield (
                line.merchant_id, minted, item["envelope_name"],
                accepts.refusal_for(minted),
            )
            continue
        destination = offered.get(item["destination"])
        if destination is None:
            continue
        # **The row's OWN name, never its period-labelled one.**  A
        # ``PurchaseDestination.label`` carries the pay period the row belongs
        # to -- *Groceries (2026-08-13 - 2026-08-26)* -- because that is what
        # an owner CHOOSING a destination needs to tell two periods' copies
        # apart.  A standing rule is the opposite question: it names the
        # TEMPLATE and applies to every period, so the label would say *always
        # file Walmart in Groceries for that fortnight*, which is not what
        # pressing it does.  Caught by rendering the offer against a
        # production clone 2026-08-30.
        named = rule_naming(line.merchant_id, destination)
        yield (
            line.merchant_id, named, destination.name,
            accepts.refusal_for(named),
        )


def _by_merchant(stated, names) -> "tuple[RuleOffer, ...]":
    """Return the stated answers tallied into one offer per merchant.

    Args:
        stated: What :func:`_stated` yielded.
        names: Each merchant's name by id, for the sentence.

    Returns:
        One :class:`RuleOffer` per merchant, in first-seen order.
    """
    # ``{merchant_id: {answer: OfferedAnswer}}`` -- keyed by the STATEMENT so
    # two lines filed into two periods' copies of one envelope collapse to the
    # single answer they are.  That collapse is why the contradiction rate on
    # the developer's own data is zero: a rule names a template, and every
    # period's row for one template is the same rule.
    by_merchant: "dict[int, dict[RuleSubmission, OfferedAnswer]]" = {}
    for merchant_id, statement, label, blocked in stated:
        answers = by_merchant.setdefault(merchant_id, {})
        seen = answers.get(statement)
        answers[statement] = OfferedAnswer(
            statement=statement,
            # **The FIRST line's label wins**, and the choice is arbitrary
            # rather than meaningful: one template can generate rows under two
            # names across periods (``template 22`` generated ``Kayla`` in one
            # and ``Kayla's Spending Money`` in the other 60), so a collapsed
            # answer has no single true name.  Taking the first at least makes
            # the offer stable across a re-render, where taking the last made
            # it depend on the pass's own ordering.
            label=label if seen is None else seen.label,
            count=1 if seen is None else seen.count + 1,
            blocked=blocked,
        )
    return tuple(
        RuleOffer(
            merchant_id=merchant_id,
            merchant=names[merchant_id],
            answers=tuple(answers.values()),
        )
        for merchant_id, answers in by_merchant.items()
    )
