"""What a standing rule comes to for this pass, and why one did not fire.

Plan step ``bank_import:X-gf-3``, finding **N-359**.  :mod:`._placement`
answers *where does this merchant's money go* for one line; :mod:`._filing` is
the door that files it without a press; and this answers the question BETWEEN
them, which neither owned: *would this pass file this line under that rule, and
if not, why not*.

**It exists because that verdict had exactly one reader and no surface.**
``_rule_filings`` decided it inside the import request and reported it on
:class:`~._filing.RuleFiling`, whose only rendering is the import's FLASH.  The
review screen then re-rendered the same lines and could say nothing about it --
so a line the owner's own rule was supposed to have handled sat in the queue
looking like one nobody had answered for.  The screen could not restate the
rule either: a second spelling of *would this have filed* is a second place for
it to be wrong, on the one door in the app that moves money with no press.  So
the verdict is derived ONCE, here, and both read it (finding **N-359**).

**Two reasons withhold a filing, and they are not the same kind of fact.**

* **The pass did not finish LOOKING** (:func:`search_gap`, developer ruling
  2026-08-26).  Membership of :attr:`~._reads.ReviewSet.creatable` is a set
  defined by SUBTRACTION -- no proposal claimed the line -- and that is two
  facts wearing one name: *the pass looked and there is nothing*, and *the pass
  threw the only candidate away*.  Under a human tick the person reading the
  screen is the check; ruling **R-GH**'s door has no person, so it withholds.
* **The rule's destination is one this statement already explains as a whole**
  (ruling **R-FZ(d)**, inverted).  That ruling settled that where two TICKED
  items collide over one envelope the PROPOSAL wins; auto-apply files at import
  and the proposal is ticked afterwards, so the same rule is applied from the
  other side.

**The TICK path is guarded at the door and this is not a second guard for it.**
:func:`~._create.create_purchase_from_line` reads ``matched_subjects`` per act
and :func:`~._container.resolve_destination` refuses a destination a match has
already claimed, so a person who accepts the proposal and then sweeps the line
into the same envelope is refused there -- in either order, since
:func:`~._candidates.repriced` re-prices every named row per act and finding
**N-336** refuses an item whose row has moved since the screen described it.
What this module adds is the SENTENCE, not a refusal: the screen says why the
rule did not fire and the owner decides, which is exactly the consent split
ruling **R-GH** made.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query -- every fact
it needs arrives from the pass that derived it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ._creations import PurchaseCreation
from ._leftovers import CreatableLine
from ._offers import BankLine, MatchProposal, RowKind
from ._pairing import DAY_WINDOW

#: What the screen and the receipt both say about a rule whose destination this
#: statement already explains on its own.  One spelling, because the door that
#: withholds and the screen that reports it are describing one decision.
_ALREADY_EXPLAINED = (
    "the budget line your rule files it into is one this statement already "
    "explains as a whole, so filing into it would count that money twice"
)


def search_gap(
    line: BankLine,
    declined_lines: "dict[int, str]",
    crowded_days: "tuple[date, ...]",
    unpriceable_count: int,
) -> "str | None":
    """Return why this pass cannot say *line* has no counterpart, or ``None``.

    Plan step ``bank_import:X-ge``, developer ruling 2026-08-26, corrected at
    ``X-ge-1``, moved here from :class:`~._reads.ReviewSet` at ``X-gf-3`` so
    the rule verdict beside it can ask the same question without importing the
    screen's own value.  :meth:`~._reads.ReviewSet.search_gap_for` is the
    screen's spelling of it and delegates here.

    **It READS what the search reports and derives nothing**, which is the
    whole of the correction ``X-ge-1`` made.  A first version enumerated the
    bounds :class:`~._reads.ReviewBounds` and the near tier PUBLISH, and called
    that enumeration complete; an adversarial review measured it false twice
    over, because the matcher applies more bounds than it published.
    Re-deriving them here would have been a third spelling of
    :data:`~._near.NEAR_MISS_BOUND` and :data:`~._pairing.DAY_WINDOW` outside
    the modules that own them -- finding **N-322** exactly, which
    :mod:`._pairing`'s own header predicts in as many words.  So each tier
    reports its own refusals now (:attr:`~._propose.ProposedMatches
    .declined_lines`) and this joins them to the two bounds that belong to the
    PASS rather than to any line.

    **What that makes true:** a tier added later must put its refusals in
    ``declined_lines`` or they are invisible, which is the same rule the search
    already keeps for its crowded days -- rather than this function having to
    be taught about it.

    The three sources, in the order a reader should hear them:

    * what a TIER declined about this line, in that tier's own words: a near
      candidate it admitted and would not choose between (the
      `$356.61`-for-one-`$178.29` shape, finding **N-335**), one it refused for
      want of the merchant in the row's label, one it refused for the day
      window, and an EXACT candidate the window refused;
    * a CROWDED day the GROUP search skipped, measured within
      :data:`~._pairing.DAY_WINDOW` of the line because that is the window
      :func:`~._propose._groups` pairs a line to a bucket across;
    * a row the amount model could not PRICE at all.  It is account-wide and so
      is this refusal: an unpriced row is absent from the candidate set
      entirely, so there is no line it can be said not to match.

    **Measured on the developer's own 378 recorded lines (2026-08-26):** the
    last two are ZERO, and the first touches 12 of the 80 lines a standing rule
    would file -- `$391.77` -- one of which is his own `Apple Music` row
    sitting one day past the window from an `Apple` line the door would
    otherwise have recorded a second time.

    Args:
        line: The bank line, which must be one this pass considered.
            **THREE surfaces take it off three different lists**, and the claim
            that "every caller takes it off ``creatable``" was already false
            when it was written: the create card reads it off
            :attr:`~._reads.ReviewSet.creatable`, the hand-build form off
            :attr:`~._reads.ReviewSet.unmatched` (which is the only one an
            inflow used to reach), and since ruling **bank_import:R-GW** the
            deposit card off :attr:`~._reads.ReviewSet.recordable_inflows`.
            What every caller does share is that the line was in THIS pass,
            which is what makes *declined_lines* answerable for it.
        declined_lines: What each tier declined about a line, by line id
            (:attr:`~._propose.ProposedMatches.declined_lines`).
        crowded_days: The days the GROUP search refused to look at, as it
            reports them (:attr:`~._propose.ProposedMatches.crowded_days`).
        unpriceable_count: How many of the account's rows the amount model
            could not price (:attr:`~._offers.Candidates.unpriceable_ids`).

    Returns:
        One sentence naming the gap, for the receipt that has to say what it
        withheld and for the screen that has to say why a line is still there;
        ``None`` when this pass searched exhaustively for a counterpart to
        *line* and found none.
    """
    declined = declined_lines.get(line.line_id)
    if declined is not None:
        return declined
    crowded = [
        day for day in crowded_days
        if abs((day - line.posted_on).days) <= DAY_WINDOW
    ]
    if crowded:
        return (
            f"{crowded[0]} held too many rows for the app to search them "
            f"for a group that adds up to this line"
        )
    if unpriceable_count:
        return (
            f"{unpriceable_count} row(s) on this account could not be priced, "
            f"so the app could not compare them against this line"
        )
    return None


def _proposed_destinations(
    proposals: "tuple[MatchProposal, ...]",
) -> "frozenset[int]":
    """Return the transaction ids this pass proposes as WHOLE-row matches.

    Ruling **R-FZ(d)** from the other side (plan step ``bank_import:X-ge``).
    That ruling settles a collision between two TICKED items -- an envelope a
    proposal names is also a destination a recorded line was aimed at -- in the
    proposal's favour, because a proposal explains money the records already
    hold against a line the bank showed, which can be re-aimed next pass.
    Auto-apply files BEFORE the proposal is ticked, so the same answer has to be
    reached by withholding rather than by ordering.

    **Only rows the proposal names WHOLE.**  A proposal naming a PURCHASE
    inside an envelope is not a claim on the envelope: the two acts name
    disjoint subjects, each match's members still sum to its own lines, and
    ``_accept._reject_parent_and_its_own_purchase`` refuses only the pairing
    where ONE act names both.  Measured on the developer's own statement: 33 of
    the 80 lines a rule would file aim at an envelope holding a purchase a
    proposal names, and withholding those would withhold the whole Groceries
    case this step exists for.  **0** aim at an envelope a proposal names
    directly, which is the population this function bounds.

    Moved here from :mod:`._filing` at plan step ``bank_import:X-gf-3``, with
    the decision that read it (finding **N-359**): the filing door and the
    review screen ask one question about one pass, and asking it inside the
    door left the screen unable to ask it at all.  It takes the PROPOSALS
    rather than the whole review set, because that is all it reads and because
    the set does not exist yet when the verdict is derived.

    Args:
        proposals: What this pass proposes.

    Returns:
        The transaction ids, empty when this pass proposes no whole-row match.
    """
    return frozenset(
        row.row_id
        for proposal in proposals
        for row in proposal.rows
        if row.kind is RowKind.TRANSACTION
    )


@dataclass(frozen=True)
class RuleVerdict:
    """What the owner's standing rule comes to for one unexplained line.

    **One value with two readers**, which is the whole of finding **N-359**'s
    remedy: :func:`~._filing._rule_filings` performs it or reports it withheld,
    and the review screen prints the same sentence beside the same line.  A
    line no rule reaches has no verdict at all rather than an empty one -- the
    absence is the answer, and a value meaning *no rule* would be a second
    spelling of ``None`` that a reader could forget to branch on.

    Attributes:
        creation: The act the rule names for this line
            (:meth:`~._placement.Placement.creation_for`), which is the same
            :class:`~._creations.PurchaseCreation` the screen's own destination
            select submits.  Never ``None``: a placement that names no
            destination reaches no line, so it produces no verdict.
        withheld: Why this pass would NOT perform it, or ``None`` when nothing
            stands in its way.  **A withheld verdict is not a refusal**: the
            create door is still open for this line and its select still
            renders, because ruling **R-GH** withholds only the act nobody
            watches.  What the sentence buys is that the person watching is
            told what the automatic door saw.
    """

    creation: PurchaseCreation
    withheld: "str | None" = None


def rule_verdicts(
    creatable: "tuple[CreatableLine, ...]",
    proposals: "tuple[MatchProposal, ...]",
    declined_lines: "dict[int, str]",
    crowded_days: "tuple[date, ...]",
    unpriceable_count: int,
) -> "dict[int, RuleVerdict]":
    """Return what a standing rule comes to for each line it reaches.

    **It asks :func:`search_gap` itself rather than taking the answers**, and
    that is what keeps the screen and ruling **R-GH**'s door describing one
    limit one way: the sentence a receipt reports as the reason a rule was
    withheld IS the sentence the screen prints beside that line, because
    neither is derived twice.

    Args:
        creatable: This pass's offerable unexplained outflows, each carrying
            the placement the owner's rule comes to for it.
        proposals: What this pass proposes, from which the whole-row
            destinations are taken (:func:`_proposed_destinations`).
        declined_lines: What each tier declined about a line, by line id.
        crowded_days: The days the GROUP search refused to look at.
        unpriceable_count: How many of the account's rows the amount model
            could not price.

    Returns:
        ``{line_id: RuleVerdict}``, holding an entry for exactly the lines a
        stated rule NAMES A DESTINATION for.

        **A rule that names no container reaches nothing**, and the three
        states that produce one are not this pass withholding anything: the
        owner has said nothing about the merchant, or said *ask me every time*,
        or said *never a purchase* -- which is a BAR that keeps the line out of
        ``creatable`` entirely (ruling **R-GJ**).  None of the three is
        reported as a withholding, because a receipt saying *your rules
        withheld this* about a merchant with no rule is false.
    """
    proposed = _proposed_destinations(proposals)
    verdicts: "dict[int, RuleVerdict]" = {}
    for item in creatable:
        placement = item.placement
        creation = (
            None if placement is None
            else placement.creation_for(item.line.line_id)
        )
        if creation is None:
            continue
        # **The gap is asked FIRST**, and the order is the one the receipt
        # reads in: a line the pass never finished looking at has not been
        # shown to collide with anything, so naming the collision first would
        # report a conclusion this pass did not reach.  It is also what lets
        # the screen print ONE sentence -- the withheld one contains the gap
        # one wherever the gap is why, so a template that prefers it hides
        # nothing.
        withheld = search_gap(
            item.line, declined_lines, crowded_days, unpriceable_count,
        )
        if withheld is None and creation.transaction_id in proposed:
            withheld = _ALREADY_EXPLAINED
        verdicts[item.line.line_id] = RuleVerdict(
            creation=creation, withheld=withheld,
        )
    return verdicts
