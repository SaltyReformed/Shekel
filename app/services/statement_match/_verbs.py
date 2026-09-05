"""The four verbs a bank line can end on, and which of them are OPEN.

Ruling **bank_import:R-HP**, plan step ``bank_import:X-gj-1a``.  Every bank
line ends with exactly one of MATCH, ADD, TRANSFER or SKIP, and the Reconcile
inbox is the lines with none yet.  The service partition underneath -- a
proposal, a creatable outflow, a recordable inflow, a barred line the owner's
own answer shuts ADD for, a parked one a source files as an account payment --
stays load-bearing and stops being the reader's vocabulary, which is the half
of ruling **R-HB** this keeps.  *It named four until plan step
``bank_import:X-gj-4c`` split the barred lines in two* (ruling
**bank_import:R-JH**), which is a count of the pass's lists rather than of the
verbs and moves with them.

**A verb is OFFERED only where its door is open, and which those are is stated
HERE rather than in a Jinja condition** (ruling **R-HW**).  ONE of the four has
no door in the app at all, and a second is shut for one class of line:

* TRANSFER waits for a door that turns a bank line INTO a transfer, which
  no module in the app has.  It is not the card ACCOUNT that is missing --
  ``AccountType.CREDIT_CARD`` is an enum member and a seeded reference row --
  it is the MODEL: paying a card is one movement between two accounts the
  owner holds, and the books still record it as ``CC Payback`` rows against
  the purchases it covers (finding **N-337**, owner ``credit_card:CC3b``,
  ruling **R-GJ**).  :mod:`._bars` PARKS such a line rather than letting it
  become spending.
* SKIP is OPEN as of plan step ``bank_import:X-gj-4b``, which lit it.  *This
  module said the verb had no door and that a skipped line would come back on
  the next visit*, which was true of a build with no store: the store and its
  two doors shipped at ``X-gj-4a`` (ruling **bank_import:R-JG**), the tab a
  recorded skip lands on at ``X-gj-4c-2``, and nothing called
  :func:`~._skipping.skip_line` until this step.  It is shut for exactly ONE
  class of line -- one a source files as paying an account the owner holds
  (ruling **bank_import:R-JI**) -- and :data:`SKIP_SHUT_PAYS_AN_ACCOUNT` is
  the sentence that DOOR raises and this module renders, which is one string
  rather than two that agree today.

**The panel renders all four anyway** (ruling **R-HW**, developer 2026-08-29):
the vocabulary is taught by the panel itself, so a verb absent from three
cards in four teaches nothing.  What a shut verb renders is its explanation
and :attr:`VerbOffer.waiting_for` -- and NO submitting control, which is what
keeps this on the right side of the line this package keeps drawing: a control
whose submission can never succeed is a defect, and a disabled tab carrying
the reason it is disabled is a disclosure.  It BOUNDS ``balance:R-ET``'s
corollary (*an affordance that cannot succeed is DELETED, not given a nicer
refusal*), which holds for a CONTROL and not for a panel whose whole content
is the explanation.

**Openness and its reason are ONE field**, so a value cannot say a verb is
open and carry a sentence explaining why it is not.  That is
:attr:`~._bars.BarredLine.answer_door`'s own idiom, and the shape
:class:`~._leftovers.RecordableInflow` refuses in as many words.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query -- every fact
here arrives from the pass that measured it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Verb(enum.Enum):
    """What one bank line ends up being.

    Ruling **bank_import:R-HP**.  Four, and they are exhaustive over what a
    bank can send: it is a row the books already hold, it is spending or
    income they did not have, it is money moved between the owner's own
    accounts, or it is deliberately explained by nothing.

    **The order of the members is the order the panel renders its tabs.**
    """

    MATCH = "match"
    ADD = "add"
    TRANSFER = "transfer"
    SKIP = "skip"

    @property
    def word(self) -> str:
        """Return the word the card's sentence OPENS on.

        Ruling **bank_import:R-HR**: the first word of the one sentence a card
        carries is the verb.  Derived here rather than as a Jinja lookup for
        the reason every partition in this package is server-side -- a
        template restating one is a second place for it to be wrong.

        Returns:
            The capitalised present-tense word.
        """
        return _WORDS[self][0]

    @property
    def past(self) -> str:
        """Return the word a SETTLED card's sentence opens on.

        The Explained and Filed-by-rules tabs render the same card with the
        sentence in the past tense, which is one word's difference and not a
        second composition; the Skipped tab JOINED them at plan step
        ``bank_import:X-gj-4c-2``, which built the card a recorded skip
        renders as (:func:`~._sentence.for_skip`).

        *This named the Skipped tab as a present reader until plan step
        ``bank_import:X-gj-4c-1``, and it was never one*: what that tab held
        was the lines a standing *never a purchase* answer barred, whose
        past-tense sentence was ``_sentence.for_parked_never`` -- deleted by
        ruling **bank_import:R-JH**, because that answer states no
        disposition.  Three of the four have a past-tense reader now, and
        :attr:`TRANSFER` has none: the table is TOTAL over the four
        (:data:`_WORDS`) rather than sized to its current callers.

        Returns:
            The capitalised past-tense word.
        """
        return _WORDS[self][1]


#: Each verb's two words, present then past.  A table rather than two
#: properties full of branches, because it is one fact per verb and the pair
#: has to stay together: the Explained tab is the To-explain tab's own
#: sentence one tense over, and a card that said "Add" where it meant "Added"
#: would be describing work still to do.
_WORDS: "dict[Verb, tuple[str, str]]" = {
    Verb.MATCH: ("Match", "Matched"),
    Verb.ADD: ("Add", "Added"),
    Verb.TRANSFER: ("Transfer", "Transferred"),
    Verb.SKIP: ("Skip", "Skipped"),
}


#: Why TRANSFER cannot be pressed yet.  It is the card ARC's work rather than
#: this one's (finding **N-337**, owner ``credit_card:CC3b``), and the sentence
#: says what is missing rather than naming a plan step, because a screen may
#: not cite one.
#:
#: **It does NOT say the card account is missing**, which an earlier draft did
#: and which is false: ``AccountType.CREDIT_CARD`` is an enum member and a
#: seeded reference row, so an owner can hold one today.  What is missing is
#: the model that makes a card PAYMENT one movement between two accounts
#: instead of paybacks against the purchases it covers.
TRANSFER_WAITS = (
    "Nothing here can pair a bank line with another of your own accounts "
    "yet. A payment to a credit card is one movement between two accounts, "
    "and the app still records it as paybacks against the purchases it "
    "covers."
)

#: Why SKIP is shut for a line whose merchant a source files as paying an
#: account the owner holds.  Ruling **bank_import:R-JI** (developer,
#: 2026-09-02), plan step ``bank_import:X-gj-4b``.
#:
#: **It lives HERE and not beside the door, so the screen and the door cannot
#: disagree about the same refusal.**  It was ``_skipping._PAYS_AN_ACCOUNT``
#: until this step, which was the only reader; lighting the verb gave it a
#: second one, and two spellings that agree today are still two spellings
#: (``CLAUDE.md`` rule 14).  :func:`~._skipping.skip_line` imports it for the
#: ``ValidationError`` it raises, and :func:`offers_for` renders it as the
#: tab's explanation -- so a crafted submission and an opened panel are
#: answered in the same words.
#:
#: **The refusal is at the DOOR and the shut tab is its disclosure**, never
#: the other way round, which is ruling **R-GJ**'s lesson one verb over: that
#: ruling cost `$7,412.94` to learn that a warning paragraph over a working
#: control is not a refusal.
#:
#: **And it is not only doctrine.**  Such a line is a
#: :class:`~._bars.BarredLine` the Reconcile page counts on its *waiting for
#: the account they paid* chip, whose label carries a COUNT and a MAGNITUDE
#: (:func:`~._reconcile._chips`).  Skipping one drops it out of the pass, so
#: that money figure falls -- a skip moving a rendered amount, which is the
#: one thing this act is supposed never to do.  Named by adversarial review
#: 2026-09-02.
SKIP_SHUT_PAYS_AN_ACCOUNT = (
    "Your bank files that line as a payment to another account you hold, so "
    "it is not explained by nothing -- the money moved between two of your "
    "own accounts.  It waits on the Transfers tab until the app can pair the "
    "two sides."
)

#: Why ADD is shut for a line this pass has PROPOSED a match for.  The pass
#: derives destinations from :attr:`~._reads.ReviewSet.unmatched`, which a
#: proposal's line is not in, so no envelope was worked out for it and there
#: is nothing for the ADD panel to offer.
ADD_SHUT_BY_A_PROPOSAL = (
    "The app found rows of yours that this line pairs with, so it did not "
    "work out where a new purchase would go."
)

#: Why MATCH is shut when the pass offers no row to match against.  It is a
#: fact about the PASS rather than the line -- the same emptiness closes it
#: for every line -- and it is a real state: an account whose every row this
#: statement already explains has nothing left to pair.
MATCH_SHUT_NO_ROWS = (
    "Your records hold no unexplained row over the days these statements "
    "cover, so there is nothing here to match this against."
)


@dataclass(frozen=True)
class VerbOffer:
    """One verb, and whether this line may end on it.

    Attributes:
        verb: Which of the four (:class:`Verb`).
        waiting_for: One sentence saying why this verb may not be pressed for
            this line, or ``None`` when it may.  **ONE field for both facts**,
            because the two-field spelling admits a value that is open and
            carries a refusal, and this package has paid for a control that
            looked available and was not: ruling **R-GJ** cost `$7,412.94` to
            learn, at the grain of one merchant.
    """

    verb: Verb
    waiting_for: "str | None"

    @property
    def is_open(self) -> bool:
        """Return whether this verb has a door that would accept the line.

        Returns:
            ``True`` when nothing is being waited for.
        """
        return self.waiting_for is None

    @property
    def is_match(self) -> bool:
        """Return whether this offer is the MATCH verb.

        **A predicate rather than a value for a template to compare**, which
        is the rule :attr:`~._panel.AddTab.records_a_purchase` states three
        lines from where the Reconcile card was spelling ``offer.verb.value ==
        'match'`` anyway.  A screen comparing an enum's own string is one
        rename away from silently rendering nothing, and this project's
        reference-table rule (``CLAUDE.md``: *ids for logic, strings for
        display only*) is the same lesson on the tables that have one.
        """
        return self.verb is Verb.MATCH

    @property
    def is_add(self) -> bool:
        """Return whether this offer is the ADD verb.

        See :attr:`is_match`.  The three predicates here do NOT partition
        :class:`Verb` -- TRANSFER has no door in this build -- so a template
        renders one arm each with no ``else``, and a verb none of them claims
        renders its explanation instead of a control.
        """
        return self.verb is Verb.ADD

    @property
    def is_skip(self) -> bool:
        """Return whether this offer is the SKIP verb.

        See :attr:`is_match` for why this is a predicate rather than a value
        for a template to compare.  Added at plan step
        ``bank_import:X-gj-4b``, which gave SKIP a door: until then the verb
        was shut on every line, so its pane rendered
        :attr:`waiting_for` and no template ever asked which verb it was.

        **An open SKIP pane renders no submitting control of its own**, and
        that is a fact about the ACT rather than an omission.  MATCH offers
        rows and ADD offers a destination because those verbs take an
        argument; a skip takes none, so the card's own OK checkbox and the
        verb radio it was left on are the whole submission.  The pane's
        content is what the act does and what it does NOT do.
        """
        return self.verb is Verb.SKIP


def offers_for(
    *, add_waits: "str | None", has_rows_to_match: bool,
    skip_waits: "str | None",
) -> "tuple[VerbOffer, ...]":
    """Return all four verbs for one line, each with its door's state.

    Ruling **bank_import:R-HW**.  **All four, always** -- the panel teaches
    the vocabulary ruling **R-HP** names, so a verb is never absent because
    this build lacks its door; it is present, explained, and carries what it
    waits for.

    **The ADD refusal is STATED by the caller and never inferred here**, which
    is :class:`~._queue.QueueRow`'s own idiom and for its reason: the builder
    knows which of the pass's lists it drew this line from, so it reads the
    refusal that mechanism's value already carries -- a parked line's
    :attr:`~._bars.BarredLine.reason`, an inflow's or a creatable line's
    :attr:`~._leftovers.CreatableLine.withheld`, or
    :data:`ADD_SHUT_BY_A_PROPOSAL`.  **Membership of ``creatable`` is NOT the
    create door's answer**, which an earlier draft of this said: a line no
    saved pay period covers stays in that list and the door refuses it by
    name (adversarial review 2026-08-29).  Dispatching on the value's type here
    instead would put the partition in two places and make this module import
    the three it would have to name.

    Args:
        add_waits: Why this line may not become new spending or income, or
            ``None`` when it may.
        has_rows_to_match: Whether this pass offers ANY unexplained app row.
            One fact about the pass that every line shares, so it is passed in
            rather than re-derived per line -- the redundant producer call
            this package treats as a DRY violation rather than a cost.
        skip_waits: Why this line may not be recorded as explained by nothing,
            or ``None`` when it may.  **Stated by the caller for the same
            reason ``add_waits`` is**, and with a sharper edge: the ONE line
            class it is ever set for (ruling **bank_import:R-JI**) is not
            derivable from any of the pass's lists, which is argued once on
            :attr:`~._reads.ReviewSet.account_payments`.

    Returns:
        One :class:`VerbOffer` per :class:`Verb`, in the enum's own order,
        which is the order the panel renders its tabs.
    """
    return (
        VerbOffer(
            verb=Verb.MATCH,
            waiting_for=None if has_rows_to_match else MATCH_SHUT_NO_ROWS,
        ),
        VerbOffer(verb=Verb.ADD, waiting_for=add_waits),
        VerbOffer(verb=Verb.TRANSFER, waiting_for=TRANSFER_WAITS),
        VerbOffer(verb=Verb.SKIP, waiting_for=skip_waits),
    )
