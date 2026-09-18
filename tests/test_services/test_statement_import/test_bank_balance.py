"""What does the BANK's own record say this account held on a day?

Plan step **bank_import:X-f6e-2**, ruling **R-GF**.  An anchored import states
a figure for a day and the recorded lines say what moved around it, so the
bank's own balance is derivable for every day the lines reach.

**The cases are the shapes the developer's real data produces**, and the ones
that matter are the ABSENCES: an account whose imports state no balance (both
of his pre-X-f6e-1 imports carry a NULL ``stated_balance``, so the report
renders with no bank column at all), and a day the lines cannot reach across a
gap between two imports.  A suite that only walked a well-covered span would
pass equally against a fold that answered a confident wrong number over a hole.
"""

from datetime import date
from decimal import Decimal

from app.enums import StatementBalanceEvidenceEnum
from app.models.statement_import import BankStatementLine
from app.models.account import AccountAnchorHistory
from app.services.statement_import import (
    RecordedRun,
    bank_balance_on,
    fold_bank_balances,
    release_anchors_from,
)

from .test_anchor import _seed_import

_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN
_CORROBORATED = StatementBalanceEvidenceEnum.CORROBORATED
_UNCORROBORATED = StatementBalanceEvidenceEnum.UNCORROBORATED


class TestTheFoldWalksFromTheAnchor:
    """The bank's balance at the end of any day the lines reach."""

    def test_the_anchors_OWN_day_is_the_figure_verbatim(
        self, app, db, seed_user,
    ):
        """The fold's fixed point: at the anchor's day nothing has been added.

        It is the one day whose answer no arithmetic can get wrong, which is
        what makes every other day checkable against it.
        """
        _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 2), "-40.00"),
                   (date(2026, 3, 3), "25.00")],
        )

        assert bank_balance_on(
            seed_user["account"].id, date(2026, 3, 3),
        ) == Decimal("1085.00")

    def test_a_LATER_day_adds_the_lines_between(self, app, db, seed_user):
        """1085.00 at 03-03, then +60.00 on 03-05, is 1145.00 at 03-05.

        03-04 carries no line, so the balance holds; 03-05 adds its own.
        """
        _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 3), "25.00"),
                   (date(2026, 3, 5), "60.00")],
        )
        account_id = seed_user["account"].id

        assert bank_balance_on(
            account_id, date(2026, 3, 4),
        ) == Decimal("1085.00")
        assert bank_balance_on(
            account_id, date(2026, 3, 5),
        ) == Decimal("1145.00")

    def test_a_day_PAST_the_recorded_span_is_not_answered(
        self, app, db, seed_user,
    ):
        """Nobody has imported that day, so nobody knows what posted on it.

        **The most tempting wrong answer in the module**: the arithmetic runs
        perfectly well past the last recorded line and reports the last known
        balance, which reads as "your bank held $1,145.00" while asserting that
        nothing moved on a day no statement covers.  Every later day would
        inherit it, so a report drawn to today would show a flat, confident,
        unfounded line.
        """
        _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 3), "25.00"),
                   (date(2026, 3, 5), "60.00")],
        )

        assert bank_balance_on(
            seed_user["account"].id, date(2026, 3, 6),
        ) is None

    def test_an_EARLIER_day_subtracts_with_no_branch_deciding_the_sign(
        self, app, db, seed_user,
    ):
        """Before the anchor the same subtraction runs the other way.

        1085.00 at 03-03 less the 03-03 line (+25.00) is 1060.00 at 03-02, and
        less 03-02's (-40.00) too is 1100.00 at 03-01.
        """
        _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 2), "-40.00"),
                   (date(2026, 3, 3), "25.00")],
        )
        account_id = seed_user["account"].id

        assert bank_balance_on(
            account_id, date(2026, 3, 2),
        ) == Decimal("1060.00")
        assert bank_balance_on(
            account_id, date(2026, 3, 1),
        ) == Decimal("1100.00")

    def test_the_SCALAR_is_a_sample_of_the_SERIES(self, app, db, seed_user):
        """One derivation, two grains -- asserted rather than assumed.

        The codebase has measured what a second walk costs: a cash scalar and
        a cash series stating one quantity stood ``$15.96`` apart on the real
        Checking account.  This pins that the two entries here cannot.
        """
        _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 2), "-40.00"),
                   (date(2026, 3, 3), "25.00"),
                   (date(2026, 3, 6), "-12.34")],
        )
        account_id = seed_user["account"].id
        days = [date(2026, 3, day) for day in range(1, 9)]

        series = fold_bank_balances(account_id, days)

        # Both directions: every day the series answers, the scalar answers
        # identically -- and every day it does NOT answer, the scalar refuses
        # too.  03-07 and 03-08 are past the recorded span, so the second half
        # is exercised rather than asserted about an empty set.
        assert set(series.balances) == set(days[:6])
        for day in days:
            assert series.balances.get(day) == bank_balance_on(
                account_id, day,
            )

    def test_it_sums_only_ITS_OWN_accounts_lines(
        self, app, db, seed_user, seed_second_user,
    ):
        """A real second account holding its own anchor and its own lines.

        A FIRING control, and it is seeded through ``seed_second_user`` rather
        than against an invented account id for the reason its sibling in
        ``test_anchor`` records: asking about a NON-EXISTENT account makes the
        anchor query answer ``None`` and execution never reaches the sums at
        all, so deleting the account filter leaves the test green.
        """
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 3, 2), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "10.00"), (date(2026, 3, 2), "20.00")],
        )
        _seed_import(
            db, seed_second_user["account"], stated="9000.00",
            effective_on=date(2026, 3, 2), evidence=_FILE_CHAIN,
            file_name="other.csv",
            lines=[(date(2026, 3, 1), "8000.00"),
                   (date(2026, 3, 2), "1.00")],
        )

        # 980.00 -- the anchor less its own 03-02 line -- and not a figure
        # carrying the other account's 8000.00.
        assert bank_balance_on(
            seed_user["account"].id, date(2026, 3, 1),
        ) == Decimal("980.00")


class TestAnAccountWithNoAnchorHasNoBankBalance:
    """The state BOTH of the developer's real imports are in.

    **The fold is TOTAL since plan step ``balance:X-bj-1b``** (ruling
    **R-BAL65**): it answered ``None`` for these accounts until then, and
    answers a value holding no priced day now -- the same fact, and one a
    reader no longer branches on.  The developer re-aimed these assertions
    with that ruling.
    """

    def test_no_import_at_all_answers_NOTHING(self, app, db, seed_user):
        """Nothing recorded, so no run and nothing to walk from."""
        folded = fold_bank_balances(
            seed_user["account"].id, [date(2026, 3, 1)],
        )

        assert folded.runs == []
        assert folded.balances == {}
        assert bank_balance_on(
            seed_user["account"].id, date(2026, 3, 1),
        ) is None

    def test_an_UNANCHORED_import_places_no_figure(self, app, db, seed_user):
        """A date-range export states TODAY's balance and anchors nothing.

        Measured: the developer's 2026-01-02..2026-03-31 export, pulled
        2026-08-23, states a figure 145 days past its own last line.  Its lines
        are recorded and its claim is stored; no day carries it -- the run is
        there, and it anchors on nothing (ruling **R-BAL64**).
        """
        _seed_import(
            db, seed_user["account"], stated="2459.60", effective_on=None,
            evidence=None, lines=[(date(2026, 3, 1), "100.00")],
        )

        folded = fold_bank_balances(
            seed_user["account"].id, [date(2026, 3, 1)],
        )

        assert folded.runs == [
            RecordedRun(date(2026, 3, 1), date(2026, 3, 1), None, ()),
        ]
        assert folded.balances == {}


class TestTheWalkAnchorsOnStandingBankLevelsOnly:
    """The domain of "what the bank's record says" is the bank's own rows.

    Plan step ``balance:X-bj-1``, developer ruling 2026-09-16 (fork F4): the
    level relation holds the owner's true-ups beside the bank's placements,
    and ``anchor + sum(lines)`` is exact only from the bank's posted end-of-day
    figure -- a typed number may carry pending items -- so an owner's level
    is never this fold's anchor, whatever its evidence rank.  And a released
    level rests on lines that changed, so it anchors nothing either.
    """

    def test_an_OWNERS_level_inside_the_run_is_not_an_anchor(
        self, app, db, seed_user,
    ):
        """Lines recorded, no placed statement, one true-up: nothing prices."""
        _seed_import(
            db, seed_user["account"], stated="2459.60", effective_on=None,
            evidence=None, lines=[(date(2026, 3, 1), "100.00"),
                                  (date(2026, 3, 2), "-40.00")],
        )
        db.session.add(AccountAnchorHistory(
            account_id=seed_user["account"].id,
            anchor_balance=Decimal("1060.00"),
            observed_on=date(2026, 3, 2),
        ))
        db.session.flush()

        folded = fold_bank_balances(
            seed_user["account"].id, [date(2026, 3, 1), date(2026, 3, 2)],
        )

        # The run is there; the owner's level anchors it on nothing.
        assert [run.anchor for run in folded.runs] == [None]
        assert folded.balances == {}

    def test_a_RELEASED_level_is_not_an_anchor(self, app, db, seed_user):
        """Withdrawn by a later import's line, the level prices no day.

        The level row is still in the relation -- append-only -- and the
        release beside it is what takes it out of the walk's domain.
        """
        _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 3), "25.00")],
        )
        assert bank_balance_on(
            seed_user["account"].id, date(2026, 3, 3),
        ) == Decimal("1085.00")
        cause = _seed_import(
            db, seed_user["account"],
            lines=[(date(2026, 3, 2), "-40.00")], file_name="later.csv",
        )
        release_anchors_from(
            seed_user["account"].id, date(2026, 3, 2), cause.id,
        )
        db.session.flush()

        folded = fold_bank_balances(
            seed_user["account"].id, [date(2026, 3, 3)],
        )

        assert [run.anchor for run in folded.runs] == [None]
        assert folded.balances == {}


class TestTheStrongestAnchorIsWalkedFrom:
    """Chosen by evidence, with recency only as a tie-break."""

    def test_a_stronger_anchor_beats_a_more_recent_weaker_one(
        self, app, db, seed_user,
    ):
        """Ordering by the ref row's id was measured BACKWARDS.

        The seed writes ``file_chain, corroborated, uncorroborated``, so ``id
        DESC`` returned the WEAKEST anchor.  The ladder is read from the enum.
        """
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "10.00")],
        )
        _seed_import(
            db, seed_user["account"], stated="9999.00",
            effective_on=date(2026, 3, 2), evidence=_UNCORROBORATED,
            file_name="later.csv", lines=[(date(2026, 3, 2), "20.00")],
        )

        (run,) = fold_bank_balances(
            seed_user["account"].id, [date(2026, 3, 1)],
        ).runs

        assert run.anchor.day == date(2026, 3, 1)
        assert run.anchor.balance == Decimal("1000.00")
        assert run.anchor.evidence is _FILE_CHAIN

    def test_the_anchors_own_evidence_travels_UNCAPPED(
        self, app, db, seed_user,
    ):
        """A report displaying a figure learns nothing, so it caps nothing.

        ``recorded_opening_before`` caps at ``corroborated`` because reaching
        an answer THERE means two statements agree.  Reading the anchor back
        for a screen is not a second statement, so a ``file_chain`` anchor
        stays ``file_chain`` here -- the distinction the two readers exist to
        keep apart.
        """
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "10.00")],
        )

        (run,) = fold_bank_balances(
            seed_user["account"].id, [date(2026, 3, 1)],
        ).runs

        assert run.anchor.evidence is _FILE_CHAIN


class TestADayTheLinesCannotREACHIsNotAnswered:
    """A gap between imports is lines nobody has imported."""

    def test_a_day_across_a_GAP_is_absent_from_the_fold(
        self, app, db, seed_user,
    ):
        """Summing across a hole yields a confident wrong number.

        Two imports covering 03-01..03-02 and 03-10..03-11 leave 03-03..03-09
        unimported.  Walking from the 03-02 anchor to 03-10 would add only the
        lines that happen to be recorded and report the result as the bank's
        balance.
        """
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 3, 2), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "10.00"), (date(2026, 3, 2), "20.00")],
        )
        _seed_import(
            db, seed_user["account"], stated=None, file_name="later.csv",
            lines=[(date(2026, 3, 10), "5.00"), (date(2026, 3, 11), "5.00")],
        )
        account_id = seed_user["account"].id

        folded = fold_bank_balances(
            account_id,
            [date(2026, 3, 2), date(2026, 3, 10), date(2026, 3, 11)],
        )

        assert folded.balances[date(2026, 3, 2)] == Decimal("1000.00")
        assert date(2026, 3, 10) not in folded.balances
        assert date(2026, 3, 11) not in folded.balances
        assert bank_balance_on(account_id, date(2026, 3, 10)) is None

    def test_two_ADJACENT_spans_MERGE_into_one_run(self, app, db, seed_user):
        """A span ending on the 2nd and one starting on the 3rd are contiguous.

        **The crossing must span BOTH**, or the test cannot see the merge.
        Its first form walked from an anchor on 03-02 to 03-03 -- a crossing of
        one day that sits inside the SECOND span alone -- so a merge written
        with ``<`` instead of ``<=`` passed it.  Measured by adversarial review
        2026-08-24.  Anchoring at 03-01 and asking for 03-04 makes the crossing
        03-02..03-04, which no single span contains, so only a real merge
        answers.  Month-by-month importing is the common case and the wrong
        answer blanks a report's whole bank column.
        """
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "10.00"), (date(2026, 3, 2), "20.00")],
        )
        _seed_import(
            db, seed_user["account"], stated=None, file_name="next.csv",
            lines=[(date(2026, 3, 3), "5.00"), (date(2026, 3, 4), "7.00")],
        )

        assert bank_balance_on(
            seed_user["account"].id, date(2026, 3, 4),
        ) == Decimal("1032.00")

    def test_a_day_INSIDE_one_span_is_answered_either_side_of_the_anchor(
        self, app, db, seed_user,
    ):
        """The crossing is empty or wholly covered, so both directions answer."""
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 3, 5), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "10.00"), (date(2026, 3, 9), "20.00")],
        )
        account_id = seed_user["account"].id

        assert bank_balance_on(
            account_id, date(2026, 3, 1),
        ) == Decimal("1000.00")
        assert bank_balance_on(
            account_id, date(2026, 3, 9),
        ) == Decimal("1020.00")


class TestASpanWhoseLinesAreGoneClaimsNoCoverage:
    """A coverage claim an import can no longer vouch for."""

    def test_a_reimport_owning_no_lines_contributes_no_span(
        self, app, db, seed_user,
    ):
        """Reproduced end to end by adversarial review 2026-08-24.

        A RE-IMPORT of an identical file records zero fresh lines and keeps the
        full span -- the developer's own second import is that shape.  Deleting
        the import that actually OWNED those lines then left the re-import's
        span still claiming them, so the walk crossed unimported days and
        reported a confident wrong balance.  It is not display-only: the same
        walk feeds ``recorded_opening_before``, which the import door solves
        each new file's effective day against.

        Here the February lines belong to one import and a second import
        declares the same span while owning none.  Only the owner's span
        counts, so the day is answered while it exists and unanswerable when it
        does not.
        """
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 1, 31), evidence=_FILE_CHAIN,
            lines=[(date(2026, 1, 31), "10.00")],
            period=(date(2026, 1, 1), date(2026, 1, 31)),
        )
        february = _seed_import(
            db, seed_user["account"], stated=None, file_name="feb.csv",
            lines=[(date(2026, 2, 15), "-150.00")],
            period=(date(2026, 2, 1), date(2026, 2, 28)),
        )
        _seed_import(
            db, seed_user["account"], stated=None, file_name="feb-again.csv",
            lines=[], period=(date(2026, 2, 1), date(2026, 2, 28)),
        )
        account_id = seed_user["account"].id

        assert bank_balance_on(
            account_id, date(2026, 2, 28),
        ) == Decimal("850.00")

        db.session.query(BankStatementLine).filter(
            BankStatementLine.import_id == february.id,
        ).delete()
        db.session.flush()

        # NOT 1000.00 -- the re-import's span no longer vouches for days whose
        # lines have gone, so the walk refuses rather than crossing the hole.
        assert bank_balance_on(account_id, date(2026, 2, 28)) is None


class TestTheWalkAnchorsPerRun:
    """One anchor per run, and the others in the run are checkpoints.

    Plan step ``balance:X-bj-1b``, finding **N-343**, rulings **R-BAL63**
    (per-run anchor, checkpoints), **R-BAL64** (a run with no level anchors
    on nothing), **R-BAL65** (the fold is total and carries the runs).  Until
    this step ONE level was chosen for the whole account by evidence, so a
    ``file_chain`` level in an old run left every day of a later, disconnected
    run unpriced -- including that run's own placed day, whose crossing is
    empty.
    """

    def test_a_disconnected_run_is_priced_from_its_OWN_anchor(
        self, app, db, seed_user,
    ):
        """N-343's shape, reproduced 2026-08-24 and priced now.

        January: ``file_chain`` 1000.00 at 01-31.  March, across an
        unimported February: an assumed 500.00 at 03-20 over +50.00 (03-15)
        and -5.00 (03-20).  The stronger January anchor cannot reach March
        and no longer has to: March walks from its own figure.  03-19 is
        500.00 less the 03-20 line, 505.00; 03-14, the day before March's
        first line, is 505.00 less the 03-15 line, 455.00 -- the balance the
        run's lines start from.  The gap stays unpriced.
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1000.00", effective_on=date(2026, 1, 31),
            evidence=_FILE_CHAIN, lines=[(date(2026, 1, 31), "10.00")],
            period=(date(2026, 1, 1), date(2026, 1, 31)),
            file_name="january.csv",
        )
        _seed_import(
            db, account, stated="500.00", effective_on=date(2026, 3, 20),
            evidence=_UNCORROBORATED, file_name="march.csv",
            lines=[(date(2026, 3, 15), "50.00"), (date(2026, 3, 20), "-5.00")],
        )

        folded = fold_bank_balances(account.id, [
            date(2026, 1, 31), date(2026, 2, 14), date(2026, 3, 13),
            date(2026, 3, 14), date(2026, 3, 15), date(2026, 3, 19),
            date(2026, 3, 20), date(2026, 3, 21),
        ])

        assert [(run.first_day, run.last_day) for run in folded.runs] == [
            (date(2026, 1, 1), date(2026, 1, 31)),
            (date(2026, 3, 15), date(2026, 3, 20)),
        ]
        assert [run.anchor.day for run in folded.runs] == [
            date(2026, 1, 31), date(2026, 3, 20),
        ]
        assert folded.balances == {
            date(2026, 1, 31): Decimal("1000.00"),
            date(2026, 3, 14): Decimal("455.00"),
            date(2026, 3, 15): Decimal("505.00"),
            date(2026, 3, 19): Decimal("505.00"),
            date(2026, 3, 20): Decimal("500.00"),
        }

    def test_each_run_chooses_the_strongest_then_the_latest_WITHIN_it(
        self, app, db, seed_user,
    ):
        """The account-wide rank, applied per run.

        Run one holds a proved level at 03-01 and an assumed one at 03-05:
        the proved one anchors it however early it is.  Run two holds two
        assumed levels, 04-10 and 04-12: the later day anchors it.
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1000.00", effective_on=date(2026, 3, 1),
            evidence=_FILE_CHAIN, lines=[(date(2026, 3, 1), "10.00")],
            file_name="proved.csv",
        )
        _seed_import(
            db, account, stated="9999.00", effective_on=date(2026, 3, 5),
            evidence=_UNCORROBORATED, file_name="assumed.csv",
            lines=[(date(2026, 3, 2), "20.00"), (date(2026, 3, 5), "1.00")],
        )
        _seed_import(
            db, account, stated="700.00", effective_on=date(2026, 4, 10),
            evidence=_UNCORROBORATED, file_name="april-early.csv",
            lines=[(date(2026, 4, 10), "5.00")],
        )
        _seed_import(
            db, account, stated="710.00", effective_on=date(2026, 4, 12),
            evidence=_UNCORROBORATED, file_name="april-late.csv",
            lines=[(date(2026, 4, 11), "3.00"), (date(2026, 4, 12), "7.00")],
        )

        first, second = fold_bank_balances(account.id, []).runs

        assert (first.anchor.day, first.anchor.file_name) == (
            date(2026, 3, 1), "proved.csv",
        )
        assert (second.anchor.day, second.anchor.file_name) == (
            date(2026, 4, 12), "april-late.csv",
        )

    def test_two_levels_on_ONE_day_are_told_apart_by_id_then_by_file(
        self, app, db, seed_user,
    ):
        """A re-import of one file places a second figure on the same day.

        ``uq_anchor_history_statement_import`` is per import (ruling
        **R-BAL56**), so the relation legitimately holds two levels for one
        day and figure.  Equal in evidence and day, the later recorded one
        (the higher id) anchors; the other is a checkpoint that agrees, and
        the file name is what tells the two apart on the page.
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1000.00", effective_on=date(2026, 3, 2),
            evidence=_CORROBORATED, file_name="first.csv",
            lines=[(date(2026, 3, 1), "10.00"), (date(2026, 3, 2), "20.00")],
        )
        _seed_import(
            db, account, stated="1000.00", effective_on=date(2026, 3, 2),
            evidence=_CORROBORATED, file_name="again.csv", lines=[],
            period=(date(2026, 3, 1), date(2026, 3, 2)),
        )

        (run,) = fold_bank_balances(account.id, []).runs

        assert run.anchor.file_name == "again.csv"
        (checkpoint,) = run.checkpoints
        assert checkpoint.file_name == "first.csv"
        assert checkpoint.day == date(2026, 3, 2)
        assert checkpoint.walked == checkpoint.observed == Decimal("1000.00")
        assert checkpoint.agrees

    def test_every_other_level_in_the_run_is_a_checkpoint_walked_minus_observed(
        self, app, db, seed_user,
    ):
        """The bank's record checked against itself.

        Anchor: proved 1000.00 at 03-05 over +100.00 (03-01), -40.00 (03-03),
        +25.00 (03-05), then +15.00 on 03-06 from a later file.  The walk
        puts 03-03 at 975.00 (1000.00 less the 03-05 line) and 03-06 at
        1015.00 (1000.00 plus the 03-06 line).  A statement that ASSUMED
        1075.00 for 03-03 is off by ``walked - observed`` = -100.00; one
        corroborated at 975.00 for 03-03 agrees; the later file's own
        1020.00 at 03-06 is off by -5.00, the same subtraction run the other
        way.  All three are listed ascending by DAY then id -- the later
        file is seeded FIRST so an id order would put it first -- each naming
        its file and carrying its own evidence, and the priced days are the
        anchor's walk untouched (ruling **R-BAL66**).
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1020.00", effective_on=date(2026, 3, 6),
            evidence=_UNCORROBORATED, file_name="later.csv",
            lines=[(date(2026, 3, 6), "15.00")],
        )
        _seed_import(
            db, account, stated="1000.00", effective_on=date(2026, 3, 5),
            evidence=_FILE_CHAIN, file_name="ytd.csv",
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 3), "-40.00"),
                   (date(2026, 3, 5), "25.00")],
        )
        _seed_import(
            db, account, stated="1075.00", effective_on=date(2026, 3, 3),
            evidence=_UNCORROBORATED, file_name="guessed.csv", lines=[],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )
        _seed_import(
            db, account, stated="975.00", effective_on=date(2026, 3, 3),
            evidence=_CORROBORATED, file_name="agreeing.csv", lines=[],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )

        folded = fold_bank_balances(
            account.id, [date(2026, 3, 3), date(2026, 3, 6)],
        )
        (run,) = folded.runs

        assert (run.first_day, run.last_day) == (
            date(2026, 3, 1), date(2026, 3, 6),
        )
        assert run.anchor.file_name == "ytd.csv"
        guessed, agreeing, later = run.checkpoints
        assert (guessed.file_name, guessed.evidence) == (
            "guessed.csv", _UNCORROBORATED,
        )
        assert guessed.observed == Decimal("1075.00")
        assert guessed.walked == Decimal("975.00")
        assert guessed.difference == Decimal("-100.00")
        assert not guessed.agrees
        assert (agreeing.file_name, agreeing.evidence) == (
            "agreeing.csv", _CORROBORATED,
        )
        assert agreeing.difference == Decimal("0.00")
        assert agreeing.agrees
        assert (later.file_name, later.day) == ("later.csv", date(2026, 3, 6))
        assert later.walked == Decimal("1015.00")
        assert later.difference == Decimal("-5.00")
        assert folded.balances == {
            date(2026, 3, 3): Decimal("975.00"),
            date(2026, 3, 6): Decimal("1015.00"),
        }

    def test_a_run_with_no_level_beside_an_anchored_one_anchors_on_NOTHING(
        self, app, db, seed_user,
    ):
        """Ruling **R-BAL64**: neither across the gap nor from a true-up.

        March is anchored; April holds lines, an owner's true-up inside it,
        and no statement figure.  April anchors on nothing and prices no day
        -- not its own days, and not the day before its first line.
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="500.00", effective_on=date(2026, 3, 20),
            evidence=_UNCORROBORATED, file_name="march.csv",
            lines=[(date(2026, 3, 20), "-5.00")],
        )
        _seed_import(
            db, account, stated=None, file_name="april.csv",
            lines=[(date(2026, 4, 1), "50.00"), (date(2026, 4, 5), "-20.00")],
        )
        db.session.add(AccountAnchorHistory(
            account_id=account.id,
            anchor_balance=Decimal("2900.00"),
            observed_on=date(2026, 4, 3),
        ))
        db.session.flush()

        folded = fold_bank_balances(account.id, [
            date(2026, 3, 20), date(2026, 3, 31), date(2026, 4, 1),
            date(2026, 4, 3), date(2026, 4, 5),
        ])

        march, april = folded.runs
        assert march.anchor is not None
        assert april.anchor is None
        assert april.checkpoints == ()
        assert folded.balances == {date(2026, 3, 20): Decimal("500.00")}

    def test_a_level_on_the_day_BEFORE_a_runs_first_line_belongs_to_the_run(
        self, app, db, seed_user,
    ):
        """The within-file bound admits ``period_start - 1``, and so does the run.

        A file whose stated figure IS its opening places its level on the day
        before its first line.  That day is reached from every level in the
        run -- the run's lines start from it -- so the level anchors the run
        and prices every day of it: 1000.00 at 02-28, then +10.00 on 03-01.
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1000.00", effective_on=date(2026, 2, 28),
            evidence=_FILE_CHAIN, lines=[(date(2026, 3, 1), "10.00")],
        )

        folded = fold_bank_balances(
            account.id, [date(2026, 2, 27), date(2026, 2, 28), date(2026, 3, 1)],
        )

        (run,) = folded.runs
        assert (run.first_day, run.anchor.day) == (
            date(2026, 3, 1), date(2026, 2, 28),
        )
        assert folded.balances == {
            date(2026, 2, 28): Decimal("1000.00"),
            date(2026, 3, 1): Decimal("1010.00"),
        }

    def test_anchor_for_names_the_anchor_that_PRICED_the_day(
        self, app, db, seed_user,
    ):
        """What the import door caps its evidence against (ruling R-BAL66).

        Two runs, two anchors: a day in each names its own run's anchor, and
        a day in the gap names none -- exactly when ``balances`` holds it.
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1000.00", effective_on=date(2026, 1, 31),
            evidence=_FILE_CHAIN, lines=[(date(2026, 1, 31), "10.00")],
            file_name="january.csv",
        )
        _seed_import(
            db, account, stated="500.00", effective_on=date(2026, 3, 20),
            evidence=_UNCORROBORATED, file_name="march.csv",
            lines=[(date(2026, 3, 20), "-5.00")],
        )
        days = [date(2026, 1, 31), date(2026, 2, 14), date(2026, 3, 19)]

        folded = fold_bank_balances(account.id, days)

        assert folded.anchor_for(date(2026, 1, 31)).file_name == "january.csv"
        assert folded.anchor_for(date(2026, 3, 19)).file_name == "march.csv"
        assert folded.anchor_for(date(2026, 2, 14)) is None
        for day in days:
            assert (folded.anchor_for(day) is None) == (
                day not in folded.balances
            )
