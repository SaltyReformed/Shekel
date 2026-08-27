"""What a standing rule comes to for one pass, and why one did not fire.

Plan step ``bank_import:X-gf-3``, finding **N-359**.  The verdict is the ONE
value ruling **R-GH**'s automatic door and the review screen both read, so the
cases here grade two things and not one: that each withholding reason fires and
that the door acts on the value rather than re-deriving it.

**The reason the last of those is a MUTATION and not a comparison.** Two
derivations of one rule agree until they drift, so asserting that the door's
sentence equals the screen's would pass on the day before the drift as happily
as on every day after it.  What cannot pass is a door handed a verdict nothing
else could have produced: if it still files the line, it never read one.
"""

from datetime import date, timedelta
from dataclasses import replace
from decimal import Decimal

import pytest

from app.services.statement_match import (
    DAY_WINDOW,
    BankLine,
    CandidateRow,
    CreatableLine,
    MerchantSection,
    NewEnvelope,
    PurchaseCreation,
    PurchaseDestination,
    ReviewBounds,
    ReviewSet,
    RowKind,
    RuleVerdict,
)
from app.services.statement_match._filing import (  # pylint: disable=protected-access
    _rule_filings,
)
from app.services.statement_match._placement import (  # pylint: disable=protected-access
    Placement,
    PlacementKind,
)
from app.services.statement_match._verdict import (  # pylint: disable=protected-access
    rule_verdicts,
)

DAY = date(2026, 8, 17)
ENVELOPE_ID = 4821


def _line(line_id=7, day=DAY):
    """Return one unexplained outflow, as the pass hands it out."""
    return BankLine(
        line_id=line_id, posted_on=day, amount=Decimal("-10.89"),
        description="POINT OF SALE DEBIT L340 (Food Lion)",
        transaction_on=None, merchant_id=1, merchant="Food Lion",
    )


def _destination(transaction_id=ENVELOPE_ID):
    """Return the budget line a template answer resolves to."""
    return PurchaseDestination(
        transaction_id=transaction_id,
        name="Groceries",
        category_id=3,
        period_start=date(2026, 8, 6),
        period_end=date(2026, 8, 19),
        pay_period_id=31,
        is_settled=False,
    )


def _creatable(placement, line_id=7, day=DAY):
    """Return one creatable line carrying *placement*."""
    return CreatableLine(
        line=_line(line_id, day),
        pay_period_id=31,
        destinations=(_destination(),),
        placement=placement,
    )


def _records_in(transaction_id=ENVELOPE_ID):
    """Return the placement a template answer produces."""
    return Placement(
        merchant="Food Lion",
        kind=PlacementKind.RECORD_IN,
        destination=_destination(transaction_id),
    )


def _row(kind=RowKind.TRANSACTION, row_id=ENVELOPE_ID, parent_id=None):
    """Return one app row a proposal may name."""
    return CandidateRow(
        kind=kind, row_id=row_id, label="Groceries",
        cash_amount=Decimal("-10.89"), settled_on=DAY, is_settled=False,
        states_own_figure=False, version_id=1, parent_id=parent_id,
    )


def _proposal(row):
    """Return a proposal naming *row* for this line."""
    from app.services.statement_match import MatchProposal  # noqa: PLC0415

    return MatchProposal(lines=(_line(),), rows=(row,), day_gap=0)


def _verdicts(creatable, **pass_facts):
    """Return the verdicts for *creatable* under the stated pass facts."""
    return rule_verdicts(
        creatable,
        pass_facts.get("proposals", ()),
        declined_lines=pass_facts.get("declined_lines", {}),
        crowded_days=pass_facts.get("crowded_days", ()),
        unpriceable_count=pass_facts.get("unpriceable_count", 0),
    )


class TestARuleThatReachesNothingProducesNoVerdict:
    """The absence is the answer, and it has to mean exactly one thing.

    A verdict exists for the lines a stated rule names a destination for, so
    ``None`` is *no rule reaches this line* and never *not asked yet*.  Both
    ways of reaching that state are graded, because a receipt saying *your
    rules withheld this* about a merchant with no rule is false -- and a screen
    printing it is worse, since the owner would go looking for an answer they
    never gave.
    """

    def test_a_line_with_NO_placement_has_no_verdict(self):
        """The owner has said nothing about this merchant."""
        assert _verdicts((_creatable(None),)) == {}

    def test_a_line_whose_rule_does_NOT_REACH_it_has_no_verdict(self):
        """A rule exists and resolves to nothing in this line's own period.

        ``UNRESOLVED`` names no destination, so there is no act to perform and
        nothing was withheld: what the screen owes such a line is the
        placement's own ``unresolved_reason``, which it already prints.
        """
        unresolved = Placement(
            merchant="Food Lion",
            kind=PlacementKind.UNRESOLVED,
            unresolved_reason="that envelope has no row in this pay period",
        )

        assert _verdicts((_creatable(unresolved),)) == {}


class TestAVerdictNamesTheActAndWhetherThePassWouldPerformIt:
    """The two halves of the value, each shown against the other."""

    def test_a_clean_line_is_NOT_withheld_and_names_the_rule_s_act(self):
        """The control the withholding cases are read against.

        Without it every case below would pass against a producer that
        withholds unconditionally.
        """
        verdicts = _verdicts((_creatable(_records_in()),))

        assert verdicts[7] == RuleVerdict(
            creation=PurchaseCreation(line_id=7, transaction_id=ENVELOPE_ID),
            withheld=None,
        )

    def test_a_NEW_ENVELOPE_answer_carries_that_arm_of_the_act(self):
        """The other arm of ``PurchaseCreation``, so neither is assumed.

        A new-envelope answer is the developer's own largest rule (Amazon, 26
        lines, `$1,323.06`), and it reaches the door as a different shape --
        no ``transaction_id`` at all -- so a verdict that only carried the
        existing-envelope arm would withhold nothing and file nothing for it.
        """
        creates = Placement(
            merchant="Food Lion",
            kind=PlacementKind.CREATE_NEW,
            new_envelope=NewEnvelope(name="Groceries", category_id=3),
        )

        verdict = _verdicts((_creatable(creates),))[7]

        assert verdict.withheld is None
        assert verdict.creation == PurchaseCreation(
            line_id=7, new_envelope=NewEnvelope(name="Groceries", category_id=3),
        )

    def test_a_line_a_TIER_declined_is_withheld_in_that_tier_s_words(self):
        """The measured arm: the pass admitted a candidate and would not pick.

        12 of the developer's own 80 rule-reachable lines, `$391.77`, on the
        2026-08-26 measurement -- including the `Apple Music` row one day past
        the window from an `Apple` line the door would otherwise have recorded
        a second time.
        """
        verdict = _verdicts(
            (_creatable(_records_in()),),
            declined_lines={7: "one of your own rows is close enough to this"},
        )[7]

        assert verdict.withheld == "one of your own rows is close enough to this"

    @pytest.mark.parametrize("offset", [0, DAY_WINDOW, -DAY_WINDOW])
    def test_a_CROWDED_day_inside_the_pairing_window_withholds(self, offset):
        """A day the group search skipped is a search that was not run.

        All three edges of the window, because the arm is the one
        :func:`~._verdict.search_gap` measures at ZERO on real data: nothing
        reachable would catch a strict-vs-inclusive slip on either end.
        """
        crowded = DAY + timedelta(days=offset)

        verdict = _verdicts(
            (_creatable(_records_in()),), crowded_days=(crowded,),
        )[7]

        assert verdict.withheld is not None
        assert str(crowded) in verdict.withheld

    def test_a_CROWDED_day_beyond_the_window_withholds_NOTHING(self):
        """...and the bound stays a bound.

        Without this the arm would withhold every line on the account the
        moment one day anywhere got crowded, and a door that files nothing is
        indistinguishable from one that is switched off.
        """
        verdict = _verdicts(
            (_creatable(_records_in()),),
            crowded_days=(DAY + timedelta(days=DAY_WINDOW + 1),),
        )[7]

        assert verdict.withheld is None

    def test_an_UNPRICEABLE_row_withholds_the_whole_account(self):
        """Account-wide blindness withholds account-wide.

        The worst of the three arms: one row the amount model cannot value
        stops every rule-reached line on the account, which is why the screen
        has to say so per line rather than leave it a count in a panel.
        """
        verdict = _verdicts(
            (_creatable(_records_in()),), unpriceable_count=2,
        )[7]

        assert verdict.withheld is not None
        assert "could not be priced" in verdict.withheld


class TestAProposalOverTheRuleSDestination:
    """Ruling **R-FZ(d)** applied from the side auto-apply arrives on.

    The pairing that matters is a proposal naming the ENVELOPE WHOLE against
    one naming a PURCHASE inside it, because the two look alike and only the
    first is a collision: 33 of the developer's own 80 rule-reachable lines aim
    at an envelope holding a proposed purchase, and withholding those would
    withhold the whole case the auto-apply door exists for.
    """

    def test_a_proposal_naming_the_ENVELOPE_withholds_the_rule(self):
        """The rule would raise the very figure the proposal matched."""
        verdict = _verdicts(
            (_creatable(_records_in()),),
            proposals=(_proposal(_row(row_id=ENVELOPE_ID)),),
        )[7]

        assert verdict.withheld is not None
        assert "count that money twice" in verdict.withheld

    def test_a_proposal_naming_a_PURCHASE_INSIDE_it_does_not(self):
        """Two acts naming neither a parent nor its own child."""
        verdict = _verdicts(
            (_creatable(_records_in()),),
            proposals=(
                _proposal(_row(
                    kind=RowKind.PURCHASE, row_id=99, parent_id=ENVELOPE_ID,
                )),
            ),
        )[7]

        assert verdict.withheld is None

    def test_a_proposal_over_a_DIFFERENT_envelope_does_not(self):
        """The bound is the destination's id and not the existence of a
        proposal.

        Without this the arm would withhold every rule-reached line on any pass
        that proposes anything at all.
        """
        verdict = _verdicts(
            (_creatable(_records_in()),),
            proposals=(_proposal(_row(row_id=ENVELOPE_ID + 1)),),
        )[7]

        assert verdict.withheld is None


class TestTheGapIsAskedBeforeTheCollision:
    """The property the review screen's single warning rests on.

    The screen prints the withheld sentence where there is one and the search
    gap otherwise, and printing both would print one sentence twice.  That is
    only safe because a verdict withheld for anything OTHER than a gap has no
    gap to hide -- so the order here is load-bearing rather than cosmetic, and
    it is graded on the case where BOTH hold.
    """

    def test_a_line_with_a_gap_AND_a_collision_reports_the_gap(self):
        """A pass that did not finish looking has concluded nothing.

        Naming the collision first would report a conclusion this pass never
        reached: the candidate it threw away might be what the line really is.
        """
        gap = "one of your own rows is close enough to this"

        verdict = _verdicts(
            (_creatable(_records_in()),),
            declined_lines={7: gap},
            proposals=(_proposal(_row(row_id=ENVELOPE_ID)),),
        )[7]

        assert verdict.withheld == gap


def _review(creatable, verdicts):
    """Return a ReviewSet carrying *creatable* and *verdicts* and nothing else."""
    return ReviewSet(
        proposals=(), unmatched=(), unmatched_rows=(),
        creatable=creatable, parked=(), recordable_inflows=(),
        merchants=MerchantSection(merchants=(), templates=()),
        bounds=ReviewBounds(
            calendar_opens=None,
            before_calendar_count=0,
            before_calendar_last_day=None,
            crowded_days=(),
            unpriceable_count=0,
        ),
        rule_verdicts=verdicts,
    )


class TestTheAutomaticDoorReadsTheVerdictRatherThanDerivingOne:
    """Finding **N-359**'s remedy, graded where it can actually fail.

    The door and the screen describing one limit two ways is what this step
    removed, and the only way to show the door is READING is to hand it a
    verdict the pass facts could not have produced.  Every case below therefore
    carries a ReviewSet whose bounds are EMPTY -- no declined line, no crowded
    day, nothing unpriceable, no proposal -- so a door that re-derived would
    file every one of them.
    """

    def test_a_verdict_carrying_a_reason_WITHHOLDS_despite_a_clean_pass(self):
        """The mutation: withheld under bounds that withhold nothing."""
        creatable = (_creatable(_records_in()),)
        review = _review(creatable, {
            7: RuleVerdict(
                creation=PurchaseCreation(
                    line_id=7, transaction_id=ENVELOPE_ID,
                ),
                withheld="a reason no bound on this pass could have produced",
            ),
        })

        creations, withheld = _rule_filings(review, frozenset({7}))

        assert creations == []
        assert [item.reason for item in withheld] == [
            "a reason no bound on this pass could have produced",
        ]

    def test_a_CLEAN_verdict_files_the_act_the_verdict_names(self):
        """...and the same door files the same line when the verdict is clean.

        Paired with the case above rather than standing alone: *nothing was
        filed* is true of a broken door as well as of a working refusal.

        **The act filed is the VERDICT's and not the placement's**, which is
        why the destination here disagrees with the one the placement resolves
        to: a door still reading ``placement.creation_for`` would file into
        ``ENVELOPE_ID``.
        """
        creatable = (_creatable(_records_in()),)
        review = _review(creatable, {
            7: RuleVerdict(
                creation=PurchaseCreation(line_id=7, transaction_id=777),
            ),
        })

        creations, withheld = _rule_filings(review, frozenset({7}))

        assert creations == [PurchaseCreation(line_id=7, transaction_id=777)]
        assert withheld == []

    def test_a_line_that_is_not_a_FRESH_swipe_is_neither_filed_nor_withheld(
        self,
    ):
        """The one narrowing that stays in the door (ruling **R-GI**).

        It belongs to the DOOR and to no screen: the review screen shows a line
        whatever import first recorded it, and only this door may act without a
        press, so *new swipe lines only* cannot move onto the verdict.  A line
        outside the fresh set is not withheld either -- nothing about it was
        decided, and a receipt naming it would tell the owner their rules had
        looked at a line from a previous month.
        """
        creatable = (_creatable(_records_in()),)
        review = _review(creatable, {
            7: RuleVerdict(
                creation=PurchaseCreation(
                    line_id=7, transaction_id=ENVELOPE_ID,
                ),
            ),
        })

        creations, withheld = _rule_filings(review, frozenset())

        assert creations == []
        assert withheld == []

    def test_a_line_with_no_verdict_is_neither_filed_nor_withheld(self):
        """No rule reaches it, so this pass withheld nothing about it."""
        review = _review((_creatable(None),), {})

        creations, withheld = _rule_filings(review, frozenset({7}))

        assert creations == []
        assert withheld == []


class TestTheScreenAndTheDoorQuoteONESentence:
    """The property the whole step buys, asserted on one derived pass.

    :func:`~._reads.review_set` derives the verdicts, so what the screen reads
    through :meth:`~._reads.ReviewSet.rule_verdict_for` and what the door
    reports on :class:`~._filing.RuleFiling` are the same object.  This grades
    that the accessor finds it rather than that the two agree -- an accessor
    keyed on the wrong id would return ``None`` for every line and the screen
    would silently go back to saying nothing.
    """

    def test_the_accessor_finds_the_verdict_by_the_line_it_is_for(self):
        """Keyed by line id, and asked with the line."""
        creatable = (_creatable(_records_in()),)
        verdict = RuleVerdict(
            creation=PurchaseCreation(line_id=7, transaction_id=ENVELOPE_ID),
            withheld="a reason",
        )

        review = _review(creatable, {7: verdict})

        assert review.rule_verdict_for(_line(7)) is verdict
        assert review.rule_verdict_for(_line(8)) is None

    def test_replacing_the_map_changes_what_the_accessor_answers(self):
        """The accessor reads the field rather than a derivation beside it."""
        review = _review((_creatable(_records_in()),), {})

        assert review.rule_verdict_for(_line(7)) is None
        assert replace(review, rule_verdicts={
            7: RuleVerdict(
                creation=PurchaseCreation(
                    line_id=7, transaction_id=ENVELOPE_ID,
                ),
            ),
        }).rule_verdict_for(_line(7)) is not None
