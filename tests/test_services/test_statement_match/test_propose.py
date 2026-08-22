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
    _day_buckets,
    propose,
)


def _offers(lines, rows):
    """Return just the PROPOSALS of one proposing pass, as a list.

    :func:`~app.services.statement_match._propose.propose` answers with a
    :class:`~app.services.statement_match.ProposedMatches` since plan step
    **X-f6a-3c-2** -- the offers PLUS the days its group search declined to
    look at, which is finding **N-322**'s fix -- and the cases below are about
    the offers.  The other half is asserted by
    :class:`TestTheSearchReportsTheDaysITSkipped`, which is where the
    population question actually lives.

    Args:
        lines: The bank lines to propose against.
        rows: The candidate rows.

    Returns:
        The proposals, as a ``list`` so an ``== []`` reads naturally.
    """
    return list(propose(lines, rows).proposals)


_DAY = date(2026, 5, 1)

#: A pay period, as every one of the developer's 62 is: 14 days, start to end.
_PERIOD = (date(2026, 5, 1), date(2026, 5, 14))


def _line(line_id, amount, posted_on=_DAY, description="ACH DEBIT",
          transaction_on=None):
    """Return one bank line.

    ``transaction_on`` defaults to ``None`` -- a source stating no separate
    transaction day, which is 179 of the developer's own 361 lines.
    """
    return BankLine(
        line_id=line_id, posted_on=posted_on,
        amount=Decimal(amount), description=description,
        transaction_on=transaction_on,
    )


def _row(row_id, amount, settled_on=_DAY, is_settled=True, label=None,
         period=_PERIOD):
    """Return one SETTLED candidate app row, whose window is that one day.

    **It carries a pay period too, because ``_candidates`` fills one on every
    row including a settled one** -- and a settled row's window is its settle
    day REGARDLESS of that period, which is the "observation beats belief" half
    of :attr:`~._offers.CandidateRow.expected_window`.  Building these without
    a period left that branch order ungraded: a mutation reading
    ``expected_on`` first passed the whole file.  Found by adversarial
    financial review 2026-08-19.
    """
    return CandidateRow(
        kind=RowKind.TRANSACTION, row_id=row_id,
        label=label or f"row {row_id}", cash_amount=Decimal(amount),
        settled_on=settled_on, is_settled=is_settled,
        expected_on=period[0], expected_through=period[1],
    )


def _bill(row_id, amount, period=_PERIOD, label=None):
    """Return one UNSETTLED transaction budgeted in *period*.

    The shape finding **N-312** is about: a row the app has never marked as
    paid, whose only claim about when the money moves is the paycheck it is
    budgeted in.  Both ends of that period travel, because the bound is the
    SPAN and not its opening day (plan step ``bank_import:X-f6a-3c``).
    """
    return CandidateRow(
        kind=RowKind.TRANSACTION, row_id=row_id,
        label=label or f"bill {row_id}", cash_amount=Decimal(amount),
        settled_on=None, is_settled=False,
        expected_on=period[0], expected_through=period[1],
    )


class TestTheWindowAccessorItself:
    """:attr:`~._offers.CandidateRow.expected_window` in its own right.

    Every bound in this module rests on this one accessor, and its three
    branches -- observation, purchase day, pay period -- plus its two absent
    cases decide what "unbounded" means.  Reached only through
    :func:`~._propose.propose` until adversarial financial review 2026-08-19
    measured that one of the absent cases could be inverted with the file
    green.
    """

    def test_an_OBSERVED_day_is_a_point_and_beats_the_period(self):
        """A settled row's window is the day it settled, whatever it budgets."""
        row = _row(1, "-25.00", settled_on=date(2026, 5, 20),
                   period=(date(2026, 5, 1), date(2026, 5, 14)))

        assert row.expected_window == (date(2026, 5, 20), date(2026, 5, 20))

    def test_a_PURCHASE_day_is_a_point(self):
        """``transaction_entries.purchased_on`` is NOT NULL, so every purchase
        has one and "undated" is true only of its cash clock."""
        purchase = CandidateRow(
            kind=RowKind.PURCHASE, row_id=1, label="Kroger",
            cash_amount=Decimal("-25.00"), settled_on=None, is_settled=False,
            parent_id=900, expected_on=_DAY, expected_through=_DAY,
        )

        assert purchase.expected_window == (_DAY, _DAY)

    def test_a_BILL_is_its_whole_pay_period(self):
        """Both ends, because the period is the whole of what the app says."""
        assert _bill(1, "-25.00").expected_window == _PERIOD

    def test_a_HALF_STATED_window_reads_as_a_POINT(self):
        """Tighter, never looser -- the direction a missing fact must fail in.

        The alternative reading, "no end means no end", would make a row with
        half a window WIDER than one with all of it.
        """
        half = CandidateRow(
            kind=RowKind.TRANSACTION, row_id=1, label="Bill",
            cash_amount=Decimal("-25.00"), settled_on=None, is_settled=False,
            expected_on=_DAY,
        )

        assert half.expected_window == (_DAY, _DAY)

    def test_a_row_with_NO_window_is_NOT_OFFERABLE(self):
        """The other reading of "no window" is finding N-312 itself.

        Unconstructible through either candidate arm -- both fill
        ``expected_on`` from a NOT NULL column -- and stated rather than left
        to a default, because "no window means no bound" is the defect this
        step exists to remove.  It joins no group either, so the two passes
        cannot disagree about what an undatable row is worth.
        """
        nowhere = CandidateRow(
            kind=RowKind.TRANSACTION, row_id=1, label="Bill",
            cash_amount=Decimal("-25.00"), settled_on=None, is_settled=False,
        )

        assert nowhere.expected_window is None
        assert _offers([_line(1, "-25.00", _DAY)], [nowhere]) == []
        assert _day_buckets([_row(2, "-5.00", _DAY), nowhere])[0][_DAY] == [
            _row(2, "-5.00", _DAY),
        ]


class TestOneLineToOneRow:
    """R-FS's first shape."""

    def test_an_equal_amount_inside_the_window_is_proposed(self):
        """The ordinary case: same figure, a few days apart."""
        proposals = _offers(
            [_line(1, "-180.00", _DAY)],
            [_row(10, "-180.00", _DAY + timedelta(days=3))],
        )

        assert len(proposals) == 1
        assert proposals[0].lines[0].line_id == 1
        assert proposals[0].rows[0].row_id == 10
        assert proposals[0].day_gap == 3

    def test_a_different_amount_is_not_proposed(self):
        """The predicate is EXACT: a near miss is not a match."""
        assert _offers(
            [_line(1, "-180.00")], [_row(10, "-180.05")],
        ) == []

    def test_outside_the_window_is_not_proposed(self):
        """One day past the bound, and the bound is what it says it is."""
        far = _DAY + timedelta(days=DAY_WINDOW + 1)
        assert _offers([_line(1, "-180.00")], [_row(10, "-180.00", far)]) == []

    def test_at_the_window_edge_is_proposed(self):
        """The control for the arm above: the bound is inclusive."""
        edge = _DAY + timedelta(days=DAY_WINDOW)
        proposals = _offers([_line(1, "-180.00")], [_row(10, "-180.00", edge)])

        assert len(proposals) == 1
        assert proposals[0].day_gap == DAY_WINDOW

    def test_a_SETTLED_row_is_bounded_by_its_settle_day_not_its_period(self):
        """An OBSERVATION beats a belief, and the branch order is the rule.

        ``_candidates`` fills a pay period on every row, settled ones
        included, so :attr:`~._offers.CandidateRow.expected_window` has to
        choose -- and reading the period first would widen a settled row's
        window from one day to a fortnight plus the slack at each end.  Here
        the line is 16 days from the row's own settle day and comfortably
        inside its pay period widened, so the two readings disagree and only
        the correct one refuses.
        """
        row = _row(10, "-180.00", settled_on=date(2026, 5, 1),
                   period=(date(2026, 5, 1), date(2026, 5, 14)))

        assert _offers([_line(1, "-180.00", date(2026, 5, 17))], [row]) == []

    def test_a_row_with_no_recorded_day_is_reachable_at_all(self):
        """The bank is the only evidence about a row nobody has settled.

        This is the arm that SETTLES a row rather than re-dating one, and it
        has to survive every bound put on it.  The row is built as production
        holds one -- budgeted in a paycheck -- because since plan step
        X-f6a-3c that paycheck is what bounds it
        (:attr:`~._offers.CandidateRow.expected_window`); the class below is
        where the bound itself is graded.
        """
        proposals = _offers(
            [_line(1, "-180.00", _DAY)], [_bill(10, "-180.00")],
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
        undated = _offers(
            [_line(1, "-180.00", _DAY)], [_bill(10, "-180.00")],
        )[0]
        agreeing = _offers(
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
        proposals = _offers(
            [_line(1, "-180.00", _DAY)],
            [
                _bill(10, "-180.00"),
                _row(11, "-180.00", _DAY + timedelta(days=2)),
            ],
        )

        assert [p.rows[0].row_id for p in proposals] == [11], (
            "the unsettled row is a LEGAL partner here -- its pay period "
            "covers the line's day -- so this is a preference and not a "
            "bound refusing one of the two"
        )


class TestAPurchaseIsNotOfferedBeforeItWasMade:
    """A purchase cannot reach the bank before the day it was bought.

    ``entry_service.update_entry`` refuses that write outright
    (``_reject_settled_before_purchase``), so a proposer blind to
    ``purchased_on`` renders an Accept button that can never succeed --
    measured at 23 such (line, undated purchase) pairs on the developer's own
    clone.  Found by adversarial security review 2026-08-17.

    **Plan step X-f6a-3a made the floor SATISFIABLE rather than merely
    refusing** (ruling **R-FW**): a match may move the purchase day onto the
    bank's own, so the pairing is offered and the correction is what the
    reviewer accepts.  The floor still refuses the pairing no write could
    legalise -- a line whose own stated transaction day is AFTER the day it
    posted, which 2 of 361 lines in the developer's OFX are.

    **Why the change is not a weakening.**  It is what stops the review screen
    presenting 14 lines worth `$1,028.66` as unexplained when the app already
    holds every one of them at the same amount and the same merchant, which is
    an invitation to record them a second time.
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

    def test_a_line_before_the_purchase_day_is_OFFERED_and_corrects_it(self):
        """The app's day is refuted by the owner's own assertion, so it moves.

        Ruling **R-FW**.  Accepting says *this line IS this purchase*, which
        says the purchase was made on or before the day the line posted -- so
        a recorded day after it is wrong, and the match corrects it rather than
        the screen leaving the line looking unexplained.
        """
        proposals = _offers(
            [_line(1, "-25.00", date(2026, 5, 1))],
            [self._purchase(10, "-25.00", date(2026, 5, 8))],
        )

        assert len(proposals) == 1
        assert proposals[0].made_on == date(2026, 5, 1)
        assert [row.row_id for row in proposals[0].redated_purchases] == [10]

    def test_it_corrects_to_the_day_the_bank_STATED(self):
        """Not to the day it cleared -- the two are different facts.

        Where the source states a transaction day, that is the day the purchase
        was MADE; the posted day is when the money left.  Recording the second
        as the first would date every card purchase to its clearing day.
        """
        proposals = _offers(
            [_line(1, "-25.00", date(2026, 5, 1),
                   transaction_on=date(2026, 4, 29))],
            [self._purchase(10, "-25.00", date(2026, 5, 8))],
        )

        assert proposals[0].made_on == date(2026, 4, 29)

    def test_a_line_no_write_could_legalise_is_still_refused(self):
        """The floor survives for the pairing the correction cannot fix.

        A source whose stated transaction day is AFTER its posting day exists
        -- 2 of 361 lines in the developer's own OFX -- and there is no day a
        match could write that satisfies ``settled_on >= purchased_on``.  So
        the proposer declines rather than rendering an Accept that raises.
        """
        assert _offers(
            [_line(1, "-25.00", date(2026, 5, 1),
                   transaction_on=date(2026, 5, 2))],
            [self._purchase(10, "-25.00", date(2026, 5, 8))],
        ) == []

    def test_a_purchase_the_bank_does_not_contradict_is_left_alone(self):
        """The bank corrects what it refutes, and nothing else.

        Measured on the developer's own statement: taking the bank's day
        unconditionally moves 27 of 44 matched purchases, 18 of them onto a
        CLEARING day because the source states no transaction day at all.
        Correcting only the contradicted ones moves 3.
        """
        proposals = _offers(
            [_line(1, "-25.00", date(2026, 5, 8),
                   transaction_on=date(2026, 5, 7))],
            [self._purchase(10, "-25.00", date(2026, 5, 1))],
        )

        assert len(proposals) == 1
        assert proposals[0].redated_purchases == ()

    def test_a_line_ON_the_purchase_day_is_offered(self):
        """The control: the floor is inclusive, as the door's own check is."""
        proposals = _offers(
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
        proposals = _offers(
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
        proposals = _offers(
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

        proposals = _offers(lines, rows)

        assert {p.lines[0].line_id: p.rows[0].row_id for p in proposals} == {
            1: 10, 2: 11, 3: 12, 4: 13, 5: 14,
        }

    def test_more_rows_than_lines_uses_the_nearest(self):
        """Three candidates, one line: the closest is the pairing."""
        proposals = _offers(
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
        proposals = _offers(
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
        proposals = _offers(
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
        exactly the one R-FV cites.  Found by adversarial design review
        2026-08-17.

        **What it composes with narrowed at plan step X-f6a-3c**: the
        unsettled member joins the days its own PAY PERIOD covers rather than
        every day on the account, so it is built here as the row production
        actually holds -- budgeted in the paycheck the deposit landed in.  The
        old "joins every day" reading is what forced a global pool over 674
        rows and switched this arm off wholesale.  What stops a coincidence is
        unchanged: the group must still sum exactly and be the only set that
        does.
        """
        proposals = _offers(
            [_line(1, "2611.90", date(2026, 8, 13))],
            [
                _bill(10, "39.54",
                      period=(date(2026, 8, 13), date(2026, 8, 26))),
                _row(11, "2572.36", date(2026, 8, 14)),
            ],
        )

        assert len(proposals) == 1
        assert {r.row_id for r in proposals[0].rows} == {10, 11}
        assert proposals[0].difference == Decimal("0.00")

    def test_a_group_that_does_not_sum_is_not_proposed(self):
        """The five-cent payroll gap: the app must not offer it as a match.

        Finding **N-239**: on 6 of 16 payroll deposits the app's rows sum
        `$0.05`-`$0.06` below what the bank paid.  Proposing that group would
        put a difference in front of the accept door, which refuses it -- so
        the honest place to stop is here.
        """
        assert _offers(
            [_line(1, "2573.43", date(2026, 5, 21))],
            [
                _row(10, "2473.38", date(2026, 5, 21)),
                _row(11, "100.00", date(2026, 5, 21)),
            ],
        ) == []

    def test_two_possible_groups_are_not_proposed(self):
        """An ambiguous proposal is a question dressed as an answer."""
        assert _offers(
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
        assert _offers(
            [_line(1, "-100.00", _DAY)],
            [
                _row(10, "-40.00", _DAY),
                _row(11, "-60.00", _DAY + timedelta(days=1)),
            ],
        ) == []

    def test_a_one_to_one_match_wins_over_a_group(self):
        """The simpler explanation of the same money is the one to show."""
        proposals = _offers(
            [_line(1, "-100.00", _DAY)],
            [
                _row(10, "-100.00", _DAY),
                _row(11, "-40.00", _DAY),
                _row(12, "-60.00", _DAY),
            ],
        )

        assert len(proposals) == 1
        assert [r.row_id for r in proposals[0].rows] == [10]


class TestAGroupOfROWSNOBODYSETTLEDSaysSo:
    """What a group of never-settled rows may and may not claim.

    Both defects here were newly REACHABLE at plan step X-f6a-3c-1: the undated
    pool it deleted had switched this arm off wholesale on any account with
    more than six unsettled rows, so nothing exercised it.  Found by
    adversarial financial review 2026-08-19.
    """

    def test_it_does_not_CONFIRM_a_day_it_never_had(self):
        """The bucket's key belongs to the rows that SETTLED on it.

        Reading it as the group's own day captioned a group of rows nobody has
        settled as *confirms the day you already had*, printed beside *marks 2
        row(s) as happened* -- the two contradicting captions
        :attr:`~._offers.MatchProposal.day_gap` was made three-valued to stop,
        reintroduced on the other arm.
        """
        rows = [
            _row(1, "-5.00", _DAY),
            _bill(10, "-100.00"),
            _bill(11, "-50.00"),
        ]

        proposals = _offers([_line(1, "-150.00", _DAY)], rows)

        assert len(proposals) == 1
        assert {r.row_id for r in proposals[0].rows} == {10, 11}
        assert proposals[0].day_gap is None
        assert not proposals[0].confirms

    def test_a_group_WITH_a_settled_member_still_states_its_gap(self):
        """The control: a settled member gives the group a day to be out by.

        A settled row joins only its OWN day's bucket, so where any member
        carries a day the bucket key IS that day.
        """
        rows = [_row(1, "-5.00", _DAY), _bill(10, "-145.00")]

        proposals = _offers(
            [_line(1, "-150.00", _DAY + timedelta(days=2))], rows,
        )

        assert len(proposals) == 1
        assert proposals[0].day_gap == 2

    def test_the_SAME_set_reachable_from_two_days_is_not_ambiguous(self):
        """One set counted twice is not two answers.

        A combo of rows nobody has settled appears in EVERY bucket its
        members' windows reach, so counting ``(day, combo)`` pairs made one
        unambiguous set look like several and the search refused it.  Measured:
        the group below is proposed with one settled row on the account and was
        REFUSED with two, the second being unrelated and two days away.
        """
        shared = [_bill(10, "-100.00"), _bill(11, "-50.00")]
        one_day = [_row(1, "-7.00", date(2026, 5, 3))]
        two_days = one_day + [_row(2, "-9.00", date(2026, 5, 5))]
        line = _line(1, "-150.00", date(2026, 5, 4))

        assert len(_offers([line], one_day + shared)) == 1
        assert len(_offers([line], two_days + shared)) == 1, (
            "an unrelated settled row two days away must not make this set "
            "ambiguous with itself"
        )


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
        assert propose(
            [], self._binary_rows(MAX_GROUP_DAY_ROWS + 1),
        ).crowded_days == (_DAY,)

    def test_a_crowded_day_proposes_no_group(self):
        """The bound is real: the group that WOULD be found is not proposed.

        The firing control for the arm above.  The target is the sum of the two
        largest rows and no other subset reaches it, so a search that ran would
        certainly propose it.
        """
        rows = self._binary_rows(MAX_GROUP_DAY_ROWS + 1)
        target = rows[-1].cash_amount + rows[-2].cash_amount
        assert all(row.cash_amount != target for row in rows)

        assert _offers([_line(1, str(target), _DAY)], rows) == []

    def test_an_unsettled_row_joins_a_day_INSIDE_its_own_window(self):
        """Ruling **R-FV**'s third arm, which the undated pool switched off.

        A split payroll deposit with one member the app has not marked as
        having happened is the ordinary shape, so a row with no settle day has
        to reach the group path.  It reaches exactly the days its own window
        covers (:func:`~._propose._day_buckets`) -- here a bill budgeted
        2026-05-01..2026-05-14 joining the 2026-05-01 bucket.

        **The target needs the unsettled member**, so a search that left it out
        would propose nothing at all; that is what makes this a control rather
        than a restatement of the dated case below.
        """
        dated = self._binary_rows(2)
        bill = _bill(500, "-1000.00")
        target = dated[0].cash_amount + bill.cash_amount

        proposals = _offers([_line(1, str(target), _DAY)], dated + [bill])

        assert len(proposals) == 1
        assert {r.row_id for r in proposals[0].rows} == {
            dated[0].row_id, bill.row_id,
        }

    def test_a_group_may_not_hold_a_row_the_ACCEPT_DOOR_would_refuse(self):
        """The per-MEMBER half, and what is left of it.

        ``_groups`` re-asks :func:`~._propose._within_window` of every member.
        Since :func:`~._propose._day_buckets` widens by the same
        :data:`DAY_WINDOW`, the window half of that test is now implied by
        membership -- what it still refuses on its own is the PURCHASE FLOOR:
        a line that posted before the purchase was made, with no day a match
        could write that satisfies ``settled_on >= purchased_on``, which
        ``entry_service.update_entry`` rejects outright.  Offering such a group
        is an Accept button that always raises.

        **The earlier version of this test was a tautology** -- it put the
        member outside the bucket, so no group was ever composed and deleting
        the clause changed nothing.  Found by adversarial financial review
        2026-08-19.
        """
        dated = self._binary_rows(2)
        # Made AFTER the line posted, and the line states a transaction day
        # later still, so no correction can legalise it (ruling R-FW).
        unreachable = CandidateRow(
            kind=RowKind.PURCHASE, row_id=600, label="Kroger",
            cash_amount=Decimal("-1000.00"), settled_on=None,
            is_settled=False, parent_id=900,
            expected_on=_DAY + timedelta(days=3),
            expected_through=_DAY + timedelta(days=3),
        )
        target = dated[0].cash_amount + unreachable.cash_amount
        line = _line(1, str(target), _DAY,
                     transaction_on=_DAY + timedelta(days=1))

        assert unreachable in _day_buckets(dated + [unreachable])[0][_DAY], (
            "the member must be IN the bucket, or this controls the "
            "bucketing rather than the per-member refusal"
        )
        assert _offers([line], dated + [unreachable]) == []

    def test_a_bill_paid_just_OUTSIDE_its_paycheck_can_still_be_grouped(self):
        """The bucket and the pair test apply the SAME slack, or they disagree.

        A bill budgeted 2026-08-13..08-26 and paid on 08-30 beside a settled
        partner is legal for the line (:func:`~._propose._within_window`
        widens the window by :data:`DAY_WINDOW`) -- and was unbuildable into a
        group while the bucket used the unwidened window, so the proposal
        vanished with nothing to report it.  A bound only one of two passes
        applies is a disagreement, not a bound.  Found by adversarial design
        review 2026-08-19.
        """
        day = date(2026, 8, 30)
        partner = _row(10, "-2572.36", settled_on=day,
                       period=(date(2026, 8, 27), date(2026, 9, 9)))
        bill = _bill(11, "-39.54",
                     period=(date(2026, 8, 13), date(2026, 8, 26)))
        target = partner.cash_amount + bill.cash_amount

        proposals = _offers([_line(1, str(target), day)], [partner, bill])

        assert len(proposals) == 1
        assert {r.row_id for r in proposals[0].rows} == {10, 11}

    def test_a_row_whose_window_misses_the_day_does_not_CROWD_it(self):
        """The firing control for the BUCKETING itself, and it needed one.

        The per-member refusal above cannot see this: it grades whether a
        composed group survives, and the bucket decides whether the day is
        searched AT ALL.  Under the deleted "an undated row joins every day"
        rule these bills would fill this day's bucket past
        :data:`MAX_GROUP_DAY_ROWS`, so the day would be passed over, the
        purely-dated group on it would be lost, and the screen would blame a
        crowded day -- which is exactly what the developer's account showed:
        51 days reported crowded and 0 groups proposed, where the real cause
        was 674 rows budgeted elsewhere.  Reverting the window test in
        :func:`~._propose._day_buckets` fails both assertions here and no
        other test in this file.
        """
        dated = self._binary_rows(2)
        elsewhere = [
            _bill(500 + i, f"-{1000 * 2 ** i}.00",
                  period=(date(2026, 7, 1), date(2026, 7, 14)))
            for i in range(MAX_GROUP_DAY_ROWS)
        ]
        target = dated[0].cash_amount + dated[1].cash_amount

        assert propose([], dated + elsewhere).crowded_days == ()
        assert len(_offers(
            [_line(1, str(target), _DAY)], dated + elsewhere,
        )) == 1

    def test_an_unsettled_PURCHASE_joins_the_day_it_was_MADE(self):
        """The other kind's window is one day, not a span.

        ``transaction_entries.purchased_on`` is NOT NULL, so a purchase is
        never truly undated (ruling **R-FW**): it belongs to the bucket for the
        day it was made and to no other.
        """
        dated = self._binary_rows(2)
        purchase = CandidateRow(
            kind=RowKind.PURCHASE, row_id=600, label="Kroger",
            cash_amount=Decimal("-1000.00"), settled_on=None,
            is_settled=False, parent_id=900, expected_on=_DAY,
            expected_through=_DAY,
        )
        target = dated[0].cash_amount + purchase.cash_amount

        assert len(_offers(
            [_line(1, str(target), _DAY)], dated + [purchase],
        )) == 1
        # ...and a purchase made two months later joins nothing on this day.
        elsewhere = CandidateRow(
            kind=RowKind.PURCHASE, row_id=601, label="Kroger",
            cash_amount=Decimal("-1000.00"), settled_on=None,
            is_settled=False, parent_id=900,
            expected_on=date(2026, 7, 1), expected_through=date(2026, 7, 1),
        )
        assert _offers(
            [_line(1, str(target), _DAY)], dated + [elsewhere],
        ) == []

    def test_a_day_crowded_BY_THE_ROWS_THAT_JOINED_IT_is_reported(self):
        """The search and the report read ONE bucketing.

        They were two implementations of "what does this day hold", each
        re-deriving the pool's membership test, and the reporting one then
        blamed crowded days for a bound that was really the pool's -- 51 of
        them on the developer's own account while the real cause was the 674
        undated rows.  Here the day carries only two settled rows of its own
        and is over the cap ONLY because unsettled rows joined it, so a
        reporter reading ``settled_on`` alone would call it uncrowded.
        """
        dated = self._binary_rows(2)
        joiners = [
            _bill(500 + i, f"-{1000 * 2 ** i}.00")
            for i in range(MAX_GROUP_DAY_ROWS - 1)
        ]
        target = joiners[0].cash_amount + joiners[1].cash_amount

        assert propose([], dated + joiners).crowded_days == (_DAY,)
        assert _offers([_line(1, str(target), _DAY)], dated + joiners) == []

    def test_an_ordinary_day_is_searched_and_not_reported(self):
        """The control for the control: inside the bound, the group is found."""
        rows = self._binary_rows(MAX_GROUP_DAY_ROWS)
        target = rows[-1].cash_amount + rows[-2].cash_amount

        assert propose([], rows).crowded_days == ()
        proposals = _offers([_line(1, str(target), _DAY)], rows)

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
        proposals = _offers(
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


class TestTheFloorIsAppliedPerPAIRAndNotPerAmountGroup:
    """A row legal against ONE line was being handed a DIFFERENT one.

    ``_one_to_one`` filters the amount group with ``any(_within_window(...))``
    -- so a row legal against at least one of the group's lines survives -- and
    :func:`_assign`'s undated arm then paired a surviving row with whichever
    line was still free, with no check of its own.  A per-GROUP survival test
    stood in for a per-PAIR legality test.

    **What makes it reachable is the window, not the floor**, and an
    adversarial review of X-f6a-3a is what established that: the FLOOR (a line
    posted before its purchase, unrescuable) needs a line whose stated
    transaction day is after its posting day, which the only adapter cannot
    produce.  The WINDOW does the work instead -- an undated purchase's window
    is the day it was MADE
    (:attr:`~._offers.CandidateRow.expected_window`), so a line more than
    :data:`DAY_WINDOW` days from it is illegal, and that is an ordinary SECU
    line.
    """

    @staticmethod
    def _undated_purchase(made_on):
        """Return an undated purchase made on *made_on*."""
        return CandidateRow(
            kind=RowKind.PURCHASE, row_id=10, label="Kroger",
            cash_amount=Decimal("-25.00"), settled_on=None, is_settled=False,
            parent_id=900, expected_on=made_on,
        )

    def test_it_is_paired_with_the_line_it_is_LEGAL_against(self):
        """Two ordinary lines share an amount; only one is near the purchase.

        The illegal line posts EARLIER, so a loop taking lines in day order
        reaches it first -- which is exactly how the shipped defect chose it.
        Revert :func:`_assign`'s per-pair check to ``undated.pop(0)`` and this
        fails.
        """
        far = _line(1, "-25.00", date(2026, 3, 1))
        near = _line(2, "-25.00", date(2026, 5, 20))
        purchase = self._undated_purchase(date(2026, 5, 18))

        proposals = _offers([far, near], [purchase])

        assert len(proposals) == 1
        assert proposals[0].lines[0].line_id == near.line_id

    def test_a_purchase_near_NEITHER_line_is_not_offered(self):
        """No line in the group is legal, so no proposal survives.

        **It is NOT a control for the ``any()`` group filter**, and a first
        docstring claimed it was: deleting that filter entirely leaves the
        whole suite green, because the per-pair tests inside :func:`_assign`
        refuse the same pairings one level down.  The filter is an
        optimisation -- it keeps the assignment's pool to rows that can pair
        with something -- and what makes the ANSWER right is the per-pair
        test.  Measured by adversarial test-quality review 2026-08-18.
        """
        first = _line(1, "-25.00", date(2026, 3, 1))
        second = _line(2, "-25.00", date(2026, 3, 4))
        purchase = self._undated_purchase(date(2026, 7, 27))

        assert _offers([first, second], [purchase]) == []


class TestAnUndatedPurchaseIsBoundedByTheDayItWasMADE:
    """:data:`DAY_WINDOW` reaches a purchase through its OTHER clock.

    **Ruling R-FW removed the only bound an undated purchase had.**  Until
    X-f6a-3a a line posted before the purchase was made could never be
    accepted, so the proposer declined it; R-FW makes that pairing legal by
    CORRECTING the purchase day, and ``_day_distance`` answers ``None`` for a
    row with no ``settled_on`` -- so nothing bounded the pairing at all.
    Measured on the developer's own statement, the three worst then re-dated a
    purchase by 39, 40 and 59 days on an exact-amount coincidence, rewriting
    the one piece of evidence that would have exposed the mis-pairing.

    A purchase always carries ``purchased_on``, so "undated" is true of its
    CASH clock and false of the purchase.  Found by two independent adversarial
    reviews 2026-08-18.
    """

    @staticmethod
    def _undated_purchase(made_on):
        """Return an undated purchase made on *made_on*."""
        return CandidateRow(
            kind=RowKind.PURCHASE, row_id=10, label="Kroger",
            cash_amount=Decimal("-25.00"), settled_on=None, is_settled=False,
            parent_id=900, expected_on=made_on,
        )

    def test_a_line_two_months_from_the_purchase_is_not_offered(self):
        """The measured defect, as a control: a May line, a July purchase."""
        line = _line(1, "-25.00", date(2026, 6, 1),
                     transaction_on=date(2026, 5, 30))

        assert _offers([line], [self._undated_purchase(date(2026, 7, 27))]) == []

    def test_a_line_inside_the_window_still_is(self):
        """The 14 pairings the step exists for are 1 to 5 days out."""
        line = _line(1, "-25.00", date(2026, 4, 24),
                     transaction_on=date(2026, 4, 23))

        proposals = _offers(
            [line], [self._undated_purchase(date(2026, 4, 29))],
        )

        assert len(proposals) == 1
        assert proposals[0].made_on == date(2026, 4, 23)


class TestAnUnsettledBillIsBoundedByItsPayPeriod:
    """Finding **N-312**, and the bound the developer ruled for it 2026-08-19.

    A row the app has never marked as paid carries no observation of when its
    money moved, and until plan step ``bank_import:X-f6a-3c`` that was read as
    *no bound at all*: any bank line sharing its amount could claim it, from
    any date whatever.

    **Measured on the developer's own production clone.**  The account holds
    610 unsettled transactions, 600 of them projections budgeted past the
    statement's last day, and up to 92 of them share one amount -- 24 identical
    `$1,910.95` mortgage transfers spanning 2026-08-27 to 2028-07-27, chosen
    between by ROW ID rather than by date.  It never fired on the first import
    only because a settled row won every amount race; remove the settled
    partner from an amount group and **44 of the statement's own lines** pair
    with a projection budgeted 48 to 148 days later, the worst a 2026-04-01
    line taking a mortgage transfer budgeted 2026-08-27.  That is the second
    import's ordinary state, and settling next month's projection against this
    month's line files real money under the wrong paycheck.

    **The bound is the row's own PAY PERIOD**, widened by :data:`DAY_WINDOW` at
    each end, and not the statement's covered span: the span is a property of
    how much history the owner happened to export, and on this data it would
    still admit a 2026-04-17 line claiming a row budgeted 2026-08-13, 118 days
    out.  Under the pay-period rule all 44 are refused, the 124 proposals the
    screen offers are unchanged, and the 4 pairings that remain reachable
    through the unsettled arm are purchases 1 to 3 days before their line.
    """

    def test_a_TRANSACTION_is_bounded_by_its_PAY_PERIOD(self):
        """**This assertion is INVERTED from what it said before X-f6a-3c**,
        on the developer's ruling of 2026-08-19, because the behaviour it
        pinned is finding **N-312**.

        It used to assert that a bill has no distance bound at all, on the
        reasoning that ``expected_on`` is a pay-period start rather than an
        observation, so bounding a bill would refuse the arm that settles a row
        nobody has marked as having happened.  Re-measured on the developer's
        own clone at X-f6a-3c: **every one of the 51 rows that arm settles is a
        PURCHASE**, already bounded by the day it was made, and 0 proposals
        name an unsettled transaction on either the first pass or the second --
        while removing the settled partner from an amount group makes **44 of
        the statement's own lines** pair with a projection budgeted 48 to 148
        days later.  The bound is the row's own PAY PERIOD, which is the whole
        of what the app asserts about when that money moves.

        BOTH directions, because the two halves are different code and only
        one of them is the shape the 44 measured pairings are.  **The lower
        half is theirs**: those lines post BEFORE the paycheck the row is
        budgeted in, because 600 of the account's unsettled rows are
        projections dated past the statement's last day.  A mutation dropping
        the lower bound alone left this file green until adversarial financial
        review 2026-08-19 built the case.
        """
        # The 44's own shape: an April line against an August paycheck.
        assert _offers(
            [_line(1, "-25.00", date(2026, 4, 1))],
            [_bill(10, "-25.00",
                   period=(date(2026, 8, 27), date(2026, 9, 9)))],
        ) == []
        # And the mirror: an August line against a January paycheck.
        assert _offers(
            [_line(1, "-25.00", date(2026, 8, 1))],
            [_bill(10, "-25.00",
                   period=(date(2026, 1, 1), date(2026, 1, 14)))],
        ) == []

    def test_a_line_inside_that_period_settles_the_bill(self):
        """The control the bound has to survive: inside the paycheck, offered.

        A statement is evidence that money moved, so a line landing inside the
        paycheck a bill is budgeted in settles it -- which is the whole point
        of admitting unsettled rows as candidates at all.  Same bill and same
        amount as the refusal above; only the pay period moves.
        """
        proposals = _offers(
            [_line(1, "-25.00", date(2026, 5, 8))], [_bill(10, "-25.00")],
        )

        assert len(proposals) == 1
        assert proposals[0].day_gap is None, (
            "the app recorded no day, so the distance is UNKNOWN rather "
            "than zero"
        )

    def test_the_line_takes_the_NEAREST_legal_bill_not_the_lowest_id(self):
        """A monthly commitment has an instance in every paycheck.

        The bound widened by :data:`DAY_WINDOW` legitimately admits two
        ADJACENT instances for one line, so bounding without ordering leaves
        the choice to ``row_id`` -- which is the second half of the same
        defect and files the money under the wrong paycheck.

        **Measured on a simulated NEXT statement** built from the developer's
        own recurring amounts against their real unsettled rows: taking the
        lowest id lands **5 of 17** proposals on a paycheck that does not
        cover the line's own day, including a `$2,562.67` deposit and a
        `-$500.00` Groceries envelope both settled against 2026-08-27..09-09
        instead of 2026-09-10..09-23.  Taking the nearest window lands 0 of 17
        outside.

        The earlier instance here has the LOWER id, so a first-legal-by-id
        pass takes it.
        """
        line = _line(1, "-500.00", date(2026, 9, 16))
        earlier = _bill(10, "-500.00",
                        period=(date(2026, 8, 27), date(2026, 9, 9)))
        later = _bill(11, "-500.00",
                      period=(date(2026, 9, 10), date(2026, 9, 23)))

        proposals = _offers([line], [earlier, later])

        assert len(proposals) == 1
        assert proposals[0].rows[0].row_id == later.row_id, (
            "both are legal through the 14-day slack; the one whose own "
            "paycheck covers the bank's day is the one the money belongs to"
        )

    def test_it_maximises_PAIRS_before_it_minimises_distance(self):
        """A proposal the owner never sees cannot be reviewed.

        The unsettled arm was a greedy loop -- first by ``row_id``, then
        briefly nearest-first -- and a greedy pass takes the best partner for
        the line in front of it and can strand a later line whose only legal
        partner it just took.  Here row 2 is nearer to line 1 and is line 2's
        ONLY legal partner, so a greedy nearest-first pass offers one proposal
        where the table offers two.  It is the rule
        :func:`~._propose._least_cost_pairing` states in bold for the dated
        arm, now shared with this one.  Found by adversarial design review
        2026-08-19.
        """
        made_early = CandidateRow(
            kind=RowKind.PURCHASE, row_id=1, label="Kroger",
            cash_amount=Decimal("-25.00"), settled_on=None, is_settled=False,
            parent_id=900, expected_on=date(2026, 4, 21),
            expected_through=date(2026, 4, 21),
        )
        made_late = CandidateRow(
            kind=RowKind.PURCHASE, row_id=2, label="Kroger",
            cash_amount=Decimal("-25.00"), settled_on=None, is_settled=False,
            parent_id=900, expected_on=date(2026, 5, 7),
            expected_through=date(2026, 5, 7),
        )

        proposals = _offers(
            [_line(1, "-25.00", date(2026, 5, 1)),
             _line(2, "-25.00", date(2026, 5, 21))],
            [made_early, made_late],
        )

        assert {p.lines[0].line_id: p.rows[0].row_id for p in proposals} == {
            1: 1, 2: 2,
        }

    def test_the_nearest_rule_does_not_reach_past_the_bound(self):
        """The control: nearest is a preference among LEGAL rows, not a bound.

        The nearer row here is outside the window entirely, so the further
        LEGAL one is still what is offered -- a distance rule that quietly
        admitted an illegal row would undo R-FY.
        """
        line = _line(1, "-500.00", date(2026, 9, 16))
        legal = _bill(10, "-500.00",
                      period=(date(2026, 9, 10), date(2026, 9, 23)))
        nearer_but_illegal = _bill(11, "-500.00",
                                   period=(date(2027, 1, 1), date(2027, 1, 14)))

        proposals = _offers([line], [nearer_but_illegal, legal])

        assert len(proposals) == 1
        assert proposals[0].rows[0].row_id == legal.row_id

    def test_the_bound_runs_to_the_period_END_and_not_its_START(self):
        """The firing control for ``expected_through``.

        A bill budgeted 2026-05-01..2026-05-14 and paid on 2026-05-24 is ten
        days past the paycheck it was budgeted in -- an ordinary late payment,
        and inside :data:`DAY_WINDOW` of the period's END.  Measured against
        its START it is 23 days out and would be refused, so a rule reading
        ``expected_on`` as a point rather than as the opening of a span throws
        this pairing away.  Every one of the developer's 62 pay periods is 14
        days long, so the two readings differ by a fortnight on every bill.
        """
        assert len(_offers(
            [_line(1, "-25.00", date(2026, 5, 24))], [_bill(10, "-25.00")],
        )) == 1
        # ...and it does END.  One day past the slack is refused, so the
        # widened span is a bound rather than a licence.
        assert _offers(
            [_line(1, "-25.00", date(2026, 5, 29))], [_bill(10, "-25.00")],
        ) == []


class TestTheSearchReportsTheDaysITSkipped:
    """Finding **N-322**: the reported bound and the applied bound are ONE.

    A day is not searched for groups when its bucket holds more rows than
    :data:`MAX_GROUP_DAY_ROWS`.  Until plan step **X-f6a-3c-2** the review
    screen recomputed those days by bucketing EVERY candidate, while the search
    itself only ever saw the rows no one-to-one proposal had claimed -- two
    populations against one cap.  The reported set was therefore a strict
    SUPERSET, and the screen could name a day "too crowded to search" that had
    in fact been searched, on the one screen whose docstrings say four times
    that a bound must never be silent.

    The remedy is that :func:`~app.services.statement_match._propose.propose`
    publishes what its own search skipped, which is what these two cases
    separate.
    """

    @staticmethod
    def _binary_rows(count, day=_DAY):
        """Return *count* same-day rows whose every subset sums uniquely."""
        return [
            _row(200 + i, f"-{2 ** i}.00", day) for i in range(count)
        ]

    def test_a_day_the_one_to_one_pass_THINS_is_not_reported(self):
        """The firing control, and the whole of N-322 in one case.

        The day holds exactly one row too many, so bucketing every candidate
        reports it.  A bank line whose amount matches one of those rows is
        paired one-to-one, which removes that row from what the group search is
        handed -- and the day then fits.  The old reader could not see that,
        because it never looked at the population the search ran on.
        """
        rows = self._binary_rows(MAX_GROUP_DAY_ROWS + 1)
        claimed = rows[0]

        # Over EVERY candidate, which is what the reader used to do, the day is
        # over the cap.
        assert propose([], rows).crowded_days == (_DAY,)

        # With one of them spoken for by a one-to-one proposal, the search
        # itself has one fewer row and the day is searched.
        pass_over = propose(
            [_line(1, str(claimed.cash_amount), _DAY)], rows,
        )
        assert len(pass_over.proposals) == 1
        assert pass_over.proposals[0].rows[0].row_id == claimed.row_id
        assert pass_over.crowded_days == ()

    def test_a_day_that_is_still_over_the_cap_is_STILL_reported(self):
        """The other side, so the case above is not just "reports nothing".

        Two rows too many rather than one, so removing the row a one-to-one
        proposal claims still leaves the day over the cap.  Without this, a
        remedy that simply stopped reporting anything would pass.
        """
        rows = self._binary_rows(MAX_GROUP_DAY_ROWS + 2)
        claimed = rows[0]

        pass_over = propose(
            [_line(1, str(claimed.cash_amount), _DAY)], rows,
        )

        assert len(pass_over.proposals) == 1
        assert pass_over.crowded_days == (_DAY,)

    def test_every_reported_day_really_went_unsearched(self):
        """The report is a bound, not a caption: no group came off that day.

        Two crowded days and one quiet one.  The quiet day's group IS proposed
        and neither crowded day's is, so the reported set names exactly the
        days a proposal was lost on.
        """
        other = _DAY + timedelta(days=1)
        quiet = _DAY + timedelta(days=2)
        crowded = (
            self._binary_rows(MAX_GROUP_DAY_ROWS + 1, _DAY)
            + [
                _row(300 + i, f"-{2 ** i}.00", other)
                for i in range(MAX_GROUP_DAY_ROWS + 1)
            ]
        )
        # **The pair's total must not be any single row's amount**, or the
        # one-to-one pass claims a row off a crowded day first and thins it
        # below the cap -- which is the very effect the case above isolates,
        # and it would silently make this one assert the wrong thing.  Every
        # crowded row is a power of two, so 9 is reachable by no single one.
        pair = [
            _row(400, "-3.00", quiet), _row(401, "-6.00", quiet),
        ]

        pass_over = propose(
            [_line(1, "-9.00", quiet)], crowded + pair,
        )

        assert pass_over.crowded_days == (_DAY, other)
        assert len(pass_over.proposals) == 1
        assert {r.row_id for r in pass_over.proposals[0].rows} == {400, 401}


class TestAProposalSaysWhichOfThreeThingsItWouldDO:
    """``review_class``, the partition the review screen's sweeps rest on.

    It had no direct test: the only control was a route case counting three
    rendered captions, which grades the property through two Jinja filters.
    The distinction it exists for is that ``day_gap`` is THREE-valued, and a
    reading that treats ``None`` as falsy collapses "marks a row as having
    happened" into "confirms the day you already had" -- which is the sweep
    that must never be one click with the safest class.  Named by adversarial
    test-quality review 2026-08-19.
    """

    def test_no_gap_at_all_is_SETTLE_and_not_confirm(self):
        """``None`` is not zero, and reading it as falsy is the whole risk."""
        proposal = _offers(
            [_line(1, "-25.00", _DAY)], [_bill(10, "-25.00")],
        )[0]

        assert proposal.day_gap is None
        assert proposal.review_class == "settle"
        assert proposal.confirms is False

    def test_a_zero_gap_is_CONFIRM(self):
        """The class it must not be collapsed into."""
        proposal = _offers(
            [_line(1, "-25.00", _DAY)], [_row(10, "-25.00", _DAY)],
        )[0]

        assert proposal.day_gap == 0
        assert proposal.review_class == "confirm"

    def test_a_real_gap_is_CORRECT(self):
        """The third, so the partition is exercised in all three arms."""
        proposal = _offers(
            [_line(1, "-25.00", _DAY)],
            [_row(10, "-25.00", _DAY + timedelta(days=3))],
        )[0]

        assert proposal.day_gap == 3
        assert proposal.review_class == "correct"

    def test_the_three_classes_PARTITION_a_mixed_pass(self):
        """Every proposal has exactly one class, and the three cover them all.

        The property the screen's captions rely on: they count by class and
        the counts must sum to the proposals, or a proposal is sweepable twice
        or not at all.
        """
        proposals = _offers(
            [
                _line(1, "-25.00", _DAY),
                _line(2, "-31.00", _DAY),
                _line(3, "-42.00", _DAY),
            ],
            [
                _bill(10, "-25.00"),
                _row(11, "-31.00", _DAY),
                _row(12, "-42.00", _DAY + timedelta(days=3)),
            ],
        )

        assert len(proposals) == 3
        classes = [p.review_class for p in proposals]
        assert sorted(classes) == ["confirm", "correct", "settle"]


def _ticked(row_id, amount, made_on, asserted_for, label=None):
    """Return one PURCHASE ticked on the reconcile panel.

    The shape the whole class below is about: ``settled_on`` is the day the
    OWNER asserted a balance for, not a day any statement showed, so the row's
    window spans from the day it was made to that assertion.  Every such row on
    an account shares one ``settled_on`` -- all 61 of the developer's carry
    ``2026-08-18`` -- which is precisely what made the ordering defect below
    invisible to a suite that built one row at a time.
    """
    return CandidateRow(
        kind=RowKind.PURCHASE, row_id=row_id,
        label=label or f"Groceries: purchase {row_id}",
        cash_amount=Decimal(amount), settled_on=asserted_for, is_settled=True,
        parent_id=900, expected_on=made_on, expected_through=made_on,
        settle_day_is_upper_bound=True,
    )


class TestReconciledRowsArePairedByTheirWINDOW:
    """Same amount, same assertion day, different purchase days.

    **The population this arc actually has.**  A reconcile tick stamps one day
    onto every row it settles, so an account's reconciled purchases are a block
    of rows sharing ``settled_on`` and differing only in the day they were
    made.  Nothing below can be graded by a case holding one such row, which is
    why the defect these tests pin shipped: the four accessor-level tests in
    ``test_candidates`` all passed against it.

    Found by three independent adversarial reviews, 2026-08-22.
    """

    _MADE = [date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]
    _ASSERTED = date(2026, 8, 18)

    def _pass(self, row_ids):
        """Return (line day, row purchase day) pairs for one proposing pass.

        *row_ids* is given explicitly because the defect was an ordering one:
        the rows were sorted on a key that had stopped describing where their
        windows sit, so the answer depended on insertion order alone.
        """
        rows = [
            _ticked(row_id, "-1910.95", made, self._ASSERTED)
            for row_id, made in zip(row_ids, self._MADE)
        ]
        lines = [
            _line(i, "-1910.95", made + timedelta(days=1))
            for i, made in enumerate(self._MADE, start=1)
        ]
        return [
            (p.lines[0].posted_on, p.rows[0].expected_on)
            for p in _offers(lines, rows)
        ]

    def test_every_line_is_paired_with_the_purchase_it_actually_was(self):
        """Three identical amounts months apart, each to its own month."""
        for posted_on, made_on in self._pass([10, 20, 30]):
            assert posted_on.month == made_on.month

    def test_the_answer_does_not_depend_on_the_rows_own_IDS(self):
        """The property the ordering defect broke.

        ``_least_cost_pairing`` requires its rows ASCENDING by the window they
        occupy.  While every settled row's window WAS its settle day, sorting
        on ``settled_on`` satisfied that by accident; once a ticked purchase's
        window opened at its purchase day, the key collapsed to ``row_id`` --
        the file order the whole assignment exists to eliminate.  Measured
        before the fix on five identical `$1,910.95` transfers: 3 of 5 lines
        paired, 1 of those to the right month, 2 left unexplained -- and an
        unexplained line is what the merchant policy offers to RECORD.
        """
        ascending = self._pass([10, 20, 30])
        descending = self._pass([30, 20, 10])

        assert ascending == descending
        assert len(ascending) == 3

    def test_a_ticked_purchase_pairs_BEYOND_the_window_from_its_settle_day(
        self,
    ):
        """The headline claim, asserted at the proposer rather than below it.

        137 days from the assertion day and one day from the purchase.  Under
        the old point rule this pass returned nothing at all, which is the
        state that sent the line to the record-a-purchase path.
        """
        row = _ticked(10, "-18.64", date(2026, 4, 1), self._ASSERTED)
        line = _line(1, "-18.64", date(2026, 4, 2))

        proposals = _offers([line], [row])

        assert len(proposals) == 1
        assert abs((self._ASSERTED - line.posted_on).days) > DAY_WINDOW

    def test_a_ticked_purchase_can_be_GROUPED_as_well_as_paired(self):
        """The group pass must honour the same window the pair pass does.

        ``_day_buckets`` filed every settled row under its settle day alone,
        so a ticked purchase's span was visible to ``_within_window`` and
        invisible to grouping -- the disagreement that function's own docstring
        calls "not a bound".  Two ticked purchases summing to one line prove
        both passes now read one window.
        """
        rows = [
            _ticked(10, "-18.64", date(2026, 4, 1), self._ASSERTED),
            _ticked(11, "-6.36", date(2026, 4, 2), self._ASSERTED),
        ]
        line = _line(1, "-25.00", date(2026, 4, 2))

        proposals = _offers([line], rows)

        assert len(proposals) == 1
        assert {r.row_id for r in proposals[0].rows} == {10, 11}
