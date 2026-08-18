"""What the app OFFERS -- the proposer, which touches no database.

Plan step **bank_import:X-f6a-2**, ruling **R-FP** (*a match is a PROPOSAL,
never a silent apply*).  :mod:`app.services.statement_match._propose` is pure:
lines and candidates in, proposals out, no query and no clock.  So these tests
are pure too, and they can therefore assert the thing that matters most about a
matcher -- **which of several equally-priced pairings it chooses** -- without
building a database state for each.

**The assignment is the subject.**  A greedy left-to-right matcher and an
optimal one agree on every input with distinct amounts, so a test suite built
only from distinct amounts would pass against either.  The developer's own data
is not like that: five monthly `$1,910.95` mortgage transfers appear on both
sides, and the whole difference between the two algorithms shows up there.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.services.statement_match import DAY_WINDOW
from app.services.statement_match._offers import (
    BankLine,
    CandidateRow,
    RowKind,
)
from app.services.statement_match._propose import (
    MAX_GROUP_DAY_ROWS,
    MAX_GROUP_UNDATED,
    propose,
    skipped_group_days,
    undated_pool_too_large,
)

_DAY = date(2026, 5, 1)


def _line(line_id, amount, posted_on=_DAY, description="ACH DEBIT"):
    """Return one bank line."""
    return BankLine(
        line_id=line_id, posted_on=posted_on,
        amount=Decimal(amount), description=description,
    )


def _row(row_id, amount, settled_on=_DAY, is_settled=True, label=None):
    """Return one candidate app row."""
    return CandidateRow(
        kind=RowKind.TRANSACTION, row_id=row_id,
        label=label or f"row {row_id}", cash_amount=Decimal(amount),
        settled_on=settled_on, is_settled=is_settled,
    )


class TestOneLineToOneRow:
    """R-FS's first shape."""

    def test_an_equal_amount_inside_the_window_is_proposed(self):
        """The ordinary case: same figure, a few days apart."""
        proposals = propose(
            [_line(1, "-180.00", _DAY)],
            [_row(10, "-180.00", _DAY + timedelta(days=3))],
        )

        assert len(proposals) == 1
        assert proposals[0].lines[0].line_id == 1
        assert proposals[0].rows[0].row_id == 10
        assert proposals[0].day_gap == 3

    def test_a_different_amount_is_not_proposed(self):
        """The predicate is EXACT: a near miss is not a match."""
        assert propose(
            [_line(1, "-180.00")], [_row(10, "-180.05")],
        ) == []

    def test_outside_the_window_is_not_proposed(self):
        """One day past the bound, and the bound is what it says it is."""
        far = _DAY + timedelta(days=DAY_WINDOW + 1)
        assert propose([_line(1, "-180.00")], [_row(10, "-180.00", far)]) == []

    def test_at_the_window_edge_is_proposed(self):
        """The control for the arm above: the bound is inclusive."""
        edge = _DAY + timedelta(days=DAY_WINDOW)
        proposals = propose([_line(1, "-180.00")], [_row(10, "-180.00", edge)])

        assert len(proposals) == 1
        assert proposals[0].day_gap == DAY_WINDOW

    def test_a_row_with_no_recorded_day_is_always_reachable(self):
        """The bank is the only evidence about a row nobody has settled.

        There is no distance from "never observed to have moved", so the window
        cannot exclude one -- and this is the arm that reaches the 11 rows
        inside the developer's own statement span that had never been marked as
        having happened.
        """
        proposals = propose(
            [_line(1, "-180.00", date(2027, 1, 1))],
            [_row(10, "-180.00", settled_on=None, is_settled=False)],
        )

        assert len(proposals) == 1
        assert proposals[0].day_gap is None

    def test_an_undated_proposal_does_not_CONFIRM_a_day(self):
        """``None`` is not zero, and reading it as zero told the user a lie.

        **This test asserted ``day_gap == 0`` until an adversarial design
        review 2026-08-17.**  ``_day_distance`` answers ``None`` for a row
        carrying no day and the proposer wrote ``or 0``, so the screen printed
        *confirms the day you already had* beside *marks 1 row(s) as happened*
        -- two captions contradicting each other on the arm built for rows
        nobody has settled.  The three states are distinct acts and the value
        type now says so.
        """
        undated = propose(
            [_line(1, "-180.00", _DAY)],
            [_row(10, "-180.00", settled_on=None, is_settled=False)],
        )[0]
        agreeing = propose(
            [_line(2, "-90.00", _DAY)], [_row(11, "-90.00", _DAY)],
        )[0]

        assert undated.day_gap is None and not undated.confirms
        assert agreeing.day_gap == 0 and agreeing.confirms

    def test_a_dated_row_outranks_an_undated_one(self):
        """A row that genuinely sits near the line wins the pairing.

        Both are legal partners, so a matcher with no preference could take
        either -- and taking the undated one would settle a row nobody has
        evidence about while leaving the real correction unmade.
        """
        proposals = propose(
            [_line(1, "-180.00", _DAY)],
            [
                _row(10, "-180.00", settled_on=None, is_settled=False),
                _row(11, "-180.00", _DAY + timedelta(days=2)),
            ],
        )

        assert [p.rows[0].row_id for p in proposals] == [11]


class TestAPurchaseIsNotOfferedBeforeItWasMade:
    """A purchase cannot reach the bank before the day it was bought.

    ``entry_service.update_entry`` refuses that write outright
    (``_reject_settled_before_purchase``), so a proposer blind to
    ``purchased_on`` renders an Accept button that can never succeed --
    measured at 23 such (line, undated purchase) pairs on the developer's own
    clone.  Found by adversarial security review 2026-08-17.
    """

    @staticmethod
    def _purchase(row_id, amount, purchased_on, settled_on=None):
        """Return one purchase candidate with a purchase day."""
        return CandidateRow(
            kind=RowKind.PURCHASE, row_id=row_id, label="Kroger",
            cash_amount=Decimal(amount), settled_on=settled_on,
            is_settled=settled_on is not None,
            parent_id=900, expected_on=purchased_on,
        )

    def test_a_line_BEFORE_the_purchase_day_is_not_offered(self):
        """The refusal the accept door would raise, declined earlier."""
        assert propose(
            [_line(1, "-25.00", date(2026, 5, 1))],
            [self._purchase(10, "-25.00", date(2026, 5, 8))],
        ) == []

    def test_a_line_ON_the_purchase_day_is_offered(self):
        """The control: the floor is inclusive, as the door's own check is."""
        proposals = propose(
            [_line(1, "-25.00", date(2026, 5, 8))],
            [self._purchase(10, "-25.00", date(2026, 5, 8))],
        )

        assert len(proposals) == 1

    def test_a_TRANSACTION_has_no_such_floor(self):
        """Only a purchase carries one; a bill may settle before its period.

        Recording money that moved before you started budgeting is a real
        thing to do, which ``status_seam.settle_day_for_status`` says in as
        many words -- so the floor must not leak onto the other kind.
        """
        proposals = propose(
            [_line(1, "-25.00", date(2026, 5, 1))],
            [CandidateRow(
                kind=RowKind.TRANSACTION, row_id=10, label="Bill",
                cash_amount=Decimal("-25.00"), settled_on=None,
                is_settled=False, expected_on=date(2026, 5, 8),
            )],
        )

        assert len(proposals) == 1


class TestRepeatedAmountsAreAssignedGlobally:
    """The mortgage-transfer shape, which is where greedy fails."""

    def test_the_pairing_minimises_total_distance(self):
        """Two lines and two rows that a first-come pass would cross.

        Lines on the 1st and the 20th, rows on the 19th and the 2nd, all four
        `$1,910.95`.  Read in order, a greedy matcher pairs line 1 with the
        FIRST equal row it meets (the 19th, 18 days away and outside the
        window, so it proposes nothing at all) -- while the cheapest complete
        pairing is 1<->2nd and 20th<->19th, one day each.
        """
        proposals = propose(
            [
                _line(1, "-1910.95", date(2026, 5, 1)),
                _line(2, "-1910.95", date(2026, 5, 20)),
            ],
            [
                _row(10, "-1910.95", date(2026, 5, 19)),
                _row(11, "-1910.95", date(2026, 5, 2)),
            ],
        )

        pairs = {p.lines[0].line_id: p.rows[0].row_id for p in proposals}
        assert pairs == {1: 11, 2: 10}
        assert [p.day_gap for p in proposals] == [1, 1]

    def test_five_monthly_transfers_pair_in_order(self):
        """The developer's own shape, at its real size.

        Five bank lines a month apart and five rows a month apart, every one
        `$1,910.95`.  The only correct answer is the order-preserving one, and
        it is the one an optimal assignment finds.
        """
        lines = [
            _line(i, "-1910.95", date(2026, month, 1))
            for i, month in enumerate((3, 4, 5, 6, 7), start=1)
        ]
        rows = [
            _row(10 + i, "-1910.95", date(2026, month, 2))
            for i, month in enumerate((3, 4, 5, 6, 7))
        ]

        proposals = propose(lines, rows)

        assert {p.lines[0].line_id: p.rows[0].row_id for p in proposals} == {
            1: 10, 2: 11, 3: 12, 4: 13, 5: 14,
        }

    def test_more_rows_than_lines_uses_the_nearest(self):
        """Three candidates, one line: the closest is the pairing."""
        proposals = propose(
            [_line(1, "-50.00", date(2026, 5, 10))],
            [
                _row(10, "-50.00", date(2026, 5, 2)),
                _row(11, "-50.00", date(2026, 5, 9)),
                _row(12, "-50.00", date(2026, 5, 20)),
            ],
        )

        assert [p.rows[0].row_id for p in proposals] == [11]

    def test_more_lines_than_rows_leaves_the_rest_unmatched(self):
        """A statement may show movements the app never recorded."""
        proposals = propose(
            [
                _line(1, "-50.00", date(2026, 5, 2)),
                _line(2, "-50.00", date(2026, 5, 3)),
            ],
            [_row(10, "-50.00", date(2026, 5, 2))],
        )

        assert len(proposals) == 1
        assert proposals[0].lines[0].line_id == 1


class TestAGroupSumsToTheLine:
    """R-FS's second shape: N app rows, one bank line."""

    def test_two_same_day_rows_summing_to_one_line(self):
        """The split payroll deposit, which is the shape this arm is for."""
        proposals = propose(
            [_line(1, "2611.90", date(2026, 8, 13))],
            [
                _row(10, "39.54", date(2026, 8, 14)),
                _row(11, "2572.36", date(2026, 8, 14)),
            ],
        )

        assert len(proposals) == 1
        assert {r.row_id for r in proposals[0].rows} == {10, 11}
        assert proposals[0].difference == Decimal("0.00")
        assert proposals[0].posts_on == date(2026, 8, 13)

    def test_a_group_may_contain_a_row_NOBODY_HAS_SETTLED(self):
        """Ruling R-FV's third arm has to reach the group path too.

        **A first implementation excluded undated rows from grouping outright**
        -- "there is nothing to group them BY" -- which made a split payroll
        deposit with one unsettled member unproposable, and that population is
        exactly the one R-FV cites (11 rows inside the developer's own
        statement span had never been marked as having happened).  An undated
        row has no day to disagree with, so it composes with any day's set;
        what stops a coincidence is that the group must still sum exactly and
        be the only set that does.  Found by adversarial design review
        2026-08-17.
        """
        proposals = propose(
            [_line(1, "2611.90", date(2026, 8, 13))],
            [
                _row(10, "39.54", settled_on=None, is_settled=False),
                _row(11, "2572.36", date(2026, 8, 14)),
            ],
        )

        assert len(proposals) == 1
        assert {r.row_id for r in proposals[0].rows} == {10, 11}
        assert proposals[0].difference == Decimal("0.00")

    def test_a_group_that_does_not_sum_is_not_proposed(self):
        """The five-cent payroll gap: the app must not offer it as a match.

        Finding **N-299**: on 6 of 16 payroll deposits the app's rows sum
        `$0.05`-`$0.06` below what the bank paid.  Proposing that group would
        put a difference in front of the accept door, which refuses it -- so
        the honest place to stop is here.
        """
        assert propose(
            [_line(1, "2573.43", date(2026, 5, 21))],
            [
                _row(10, "2473.38", date(2026, 5, 21)),
                _row(11, "100.00", date(2026, 5, 21)),
            ],
        ) == []

    def test_two_possible_groups_are_not_proposed(self):
        """An ambiguous proposal is a question dressed as an answer."""
        assert propose(
            [_line(1, "-100.00", _DAY)],
            [
                _row(10, "-40.00", _DAY),
                _row(11, "-60.00", _DAY),
                _row(12, "-30.00", _DAY),
                _row(13, "-70.00", _DAY),
            ],
        ) == []

    def test_rows_on_different_days_are_not_grouped(self):
        """A group is what the bank took as ONE movement, on one day.

        Rows scattered across days that happen to sum to a line are a
        coincidence, and grouping them would settle several unrelated rows on a
        day none of them belongs to.
        """
        assert propose(
            [_line(1, "-100.00", _DAY)],
            [
                _row(10, "-40.00", _DAY),
                _row(11, "-60.00", _DAY + timedelta(days=1)),
            ],
        ) == []

    def test_a_one_to_one_match_wins_over_a_group(self):
        """The simpler explanation of the same money is the one to show."""
        proposals = propose(
            [_line(1, "-100.00", _DAY)],
            [
                _row(10, "-100.00", _DAY),
                _row(11, "-40.00", _DAY),
                _row(12, "-60.00", _DAY),
            ],
        )

        assert len(proposals) == 1
        assert [r.row_id for r in proposals[0].rows] == [10]


class TestTheGroupSearchSaysWhatItSkipped:
    """A bound that does not report itself reads as a clean sweep.

    **The amounts here are POWERS OF TWO deliberately.**  A first draft used
    1, 2, 3, ... and its control was a tautology: removing the bound left the
    test passing, because with consecutive amounts many subsets reach the same
    total and the search declines an AMBIGUOUS group anyway.  Powers of two
    make every subset sum unique, so the only thing that can stop the proposal
    is the bound itself -- which is what a firing control has to be.  Caught by
    mutating the bound away and finding the suite still green.
    """

    @staticmethod
    def _binary_rows(count):
        """Return *count* same-day rows whose every subset sums uniquely."""
        return [
            _row(100 + i, f"-{2 ** i}.00", _DAY) for i in range(count)
        ]

    def test_a_crowded_day_is_reported(self):
        """More rows on one day than the search will combine."""
        assert skipped_group_days(
            self._binary_rows(MAX_GROUP_DAY_ROWS + 1),
        ) == [_DAY]

    def test_a_crowded_day_proposes_no_group(self):
        """The bound is real: the group that WOULD be found is not proposed.

        The firing control for the arm above.  The target is the sum of the two
        largest rows and no other subset reaches it, so a search that ran would
        certainly propose it.
        """
        rows = self._binary_rows(MAX_GROUP_DAY_ROWS + 1)
        target = rows[-1].cash_amount + rows[-2].cash_amount
        assert all(row.cash_amount != target for row in rows)

        assert propose([_line(1, str(target), _DAY)], rows) == []

    def test_a_LARGE_UNDATED_POOL_is_kept_out_and_reported(self):
        """The second bound, and the one whose absence killed the arm.

        An undated row joins EVERY day's bucket, so admitting all of them put
        every day over :data:`MAX_GROUP_DAY_ROWS` on the developer's own
        account -- 674 undated candidates -- and the group arm proposed
        NOTHING while the screen blamed 51 "crowded days".  Measured by
        adversarial financial review 2026-08-17 against a working
        implementation that proposed 3 groups on the same data.
        """
        dated = self._binary_rows(2)
        # DISTINCT amounts, so exactly one subset reaches the target below.
        # A first draft gave them all the same figure, which made the group
        # AMBIGUOUS and the search decline it for that reason instead -- so the
        # test passed with the bound removed and controlled nothing.
        undated = [
            CandidateRow(
                kind=RowKind.TRANSACTION, row_id=500 + i, label=f"u{i}",
                cash_amount=-Decimal(1000 * 2 ** i), settled_on=None,
                is_settled=False,
            )
            for i in range(MAX_GROUP_UNDATED + 1)
        ]
        # Reachable ONLY by combining a dated row with an undated one, so the
        # dated group beside it is not what answers.
        needs_an_undated_member = dated[0].cash_amount + undated[0].cash_amount

        assert undated_pool_too_large(dated + undated) == len(undated)
        assert skipped_group_days(dated + undated) == [], (
            "the DAY is not crowded -- the undated pool is, and saying "
            "otherwise blames the wrong thing"
        )
        assert propose(
            [_line(1, str(needs_an_undated_member), _DAY)], dated + undated,
        ) == []
        # ...while the group that needs no undated member is still proposed,
        # which is the half the unbounded implementation destroyed.
        dated_only = dated[0].cash_amount + dated[1].cash_amount
        assert len(propose(
            [_line(2, str(dated_only), _DAY)], dated + undated,
        )) == 1

    def test_a_SMALL_undated_pool_still_joins_the_search(self):
        """The control: the bound admits the shape the arm exists for."""
        dated = self._binary_rows(2)
        target = dated[0].cash_amount + dated[1].cash_amount

        assert undated_pool_too_large(dated) == 0
        assert len(propose([_line(1, str(target), _DAY)], dated)) == 1

    def test_an_ordinary_day_is_searched_and_not_reported(self):
        """The control for the control: inside the bound, the group is found."""
        rows = self._binary_rows(MAX_GROUP_DAY_ROWS)
        target = rows[-1].cash_amount + rows[-2].cash_amount

        assert skipped_group_days(rows) == []
        proposals = propose([_line(1, str(target), _DAY)], rows)

        assert len(proposals) == 1
        assert {r.row_id for r in proposals[0].rows} == {
            rows[-1].row_id, rows[-2].row_id,
        }


class TestTheOrderPutsConfirmationsFirst:
    """What a reviewer scans first."""

    def test_proposals_are_ordered_by_day_gap(self):
        """A proposal that merely confirms is cheaper to review than one that
        corrects, so the corrections are what a reviewer's attention reaches
        last rather than what it has to dig for."""
        proposals = propose(
            [
                _line(1, "-10.00", date(2026, 5, 1)),
                _line(2, "-20.00", date(2026, 5, 2)),
            ],
            [
                _row(10, "-10.00", date(2026, 5, 6)),
                _row(11, "-20.00", date(2026, 5, 2)),
            ],
        )

        assert [p.day_gap for p in proposals] == [0, 5]
