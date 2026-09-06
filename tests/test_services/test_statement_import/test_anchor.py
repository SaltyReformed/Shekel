"""Which day is a statement's stated balance for, and how firmly is it held?

Plan step **bank_import:X-f6e-1**, ruling **R-GF**.  A bank writes its balance
as of the EXPORT INSTANT and labels it with the export's own day, so the header
day is not the day the figure is for -- and the file's own lines are what solve
it.

**Every case below is a shape MEASURED on the developer's real exports**, and
each one is a different answer, which is why the arms are separable at all:

* 2026-08-22 -- header ``2459.60`` as of 08-22, last line 08-21, solves at
  08-21.  The ordinary export.
* 2026-08-16 -- header ``4747.63`` as of 08-16 over a file listing two 08-14
  lines worth ``-1006.72``; the figure is 08-13's closing and solves there.
  The LAG.
* 2026-01-02..2026-03-31 pulled 2026-08-23 -- header ``2459.60`` as of 08-23,
  145 days past the last line and ``$255.41`` from the ``$2,715.01`` its own
  139 lines imply.  Solves nowhere, and refusing it would reject an honest
  export.  The RANGE.

**The refusal arm is a FIRING CONTROL** (``docs/plans/verification.md``
standard 4): nothing in ordinary use reaches it, because the only files that do
are ones whose own running-balance chain contradicts their own header, so a
test asserting only that good files pass would pass equally against a solver
that never refused.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatementBalanceEvidenceEnum, StatementSourceEnum
from app.exceptions import StatementBalanceUnexplained
from app.models.statement_import import BankStatementLine, StatementImport
from app.services.statement_import import (
    KnownOpening,
    StatementLine,
    recorded_opening_before,
    release_anchors_from,
    resolve_anchor,
    solve_effective_day,
    weaker_of,
)

_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN
_CORROBORATED = StatementBalanceEvidenceEnum.CORROBORATED
_UNCORROBORATED = StatementBalanceEvidenceEnum.UNCORROBORATED


def _line(day, amount, running=None):
    """Return one line, optionally carrying a running balance."""
    return StatementLine(
        posted_on=day,
        transaction_on=None,
        amount=Decimal(amount),
        description="X",
        merchant=None,
        source_category=None,
        external_id=None,
        running_balance=None if running is None else Decimal(running),
    )


def _chain(opening, moves, first_day=date(2026, 3, 1)):
    """Return lines whose running balances FOLLOW from *opening*, one per day."""
    balance = Decimal(opening)
    built = []
    for offset, amount in enumerate(moves):
        balance += Decimal(amount)
        built.append(
            _line(date.fromordinal(first_day.toordinal() + offset),
                  amount, balance)
        )
    return built


def _plain(moves, first_day=date(2026, 3, 1)):
    """Return lines with NO running balance -- every modern SECU export."""
    return [
        _line(date.fromordinal(first_day.toordinal() + offset), amount)
        for offset, amount in enumerate(moves)
    ]


def _known(amount, evidence=_CORROBORATED):
    """Return a :class:`KnownOpening` for a solve to run against."""
    return KnownOpening(amount=Decimal(amount), evidence=evidence)


def _seed_import(db, account, *, stated=None, effective_on=None,
                 evidence=None, lines=(), period=None, file_name="seed.csv"):
    """Record one import and its lines directly, anchored or not.

    Built through the models rather than through ``record_statement``, and
    deliberately: these exercise the WALK and the RELEASE over what is stored,
    so constructing the stored state directly keeps their tests from also being
    tests of the door that writes it.

    Args:
        db: The session fixture.
        account: The account to record against.
        stated: The header figure, or ``None`` for a file stating none.
        effective_on: The day it is placed at, or ``None`` for unanchored.
        evidence: The evidence member, required with *effective_on*.
        lines: ``(day, amount)`` pairs, chronological.
        period: ``(start, end)``, defaulting to the lines' own extremes.
        file_name: Provenance, and the digest's seed.

    Returns:
        The staged :class:`~app.models.statement_import.StatementImport`.
    """
    days = [day for day, _ in lines]
    start, end = period or (min(days), max(days))
    row = StatementImport(
        account_id=account.id,
        user_id=account.user_id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        file_name=file_name,
        file_digest=file_name.ljust(64, "0")[:64],
        period_start=start,
        period_end=end,
        line_count=max(len(lines), 1),
        recorded_count=len(lines),
        stated_balance=None if stated is None else Decimal(stated),
        stated_balance_on=None if stated is None else (effective_on or end),
        balance_effective_on=effective_on,
        balance_evidence_id=(
            None if evidence is None
            else ref_cache.statement_balance_evidence_id(evidence)
        ),
    )
    db.session.add(row)
    db.session.flush()
    for ordinal, (day, amount) in enumerate(lines):
        db.session.add(BankStatementLine(
            account_id=account.id,
            import_id=row.id,
            posted_on=day,
            transaction_on=None,
            amount=Decimal(amount),
            description="X",
            merchant=None,
            source_category=None,
            external_id=None,
            sequence_in_group=ordinal,
            running_balance=None,
        ))
    db.session.flush()
    return row


class TestTheSolveFindsTheDayTheFigureIsFor:
    """``stated - sum(lines up to d) == opening``, over the file's own days."""

    def test_a_figure_matching_the_last_day_solves_there(self):
        """The ordinary export: the header is the closing after everything.

        Arithmetic: opening 1000.00, then +100.00, -40.00, +25.00, so the
        cumulative figures are 1100.00 / 1060.00 / 1085.00 and only the last
        matches.
        """
        lines = _plain(["100.00", "-40.00", "25.00"])

        assert solve_effective_day(
            lines, Decimal("1085.00"), Decimal("1000.00"), date(2026, 3, 9),
        ) == date(2026, 3, 3)

    def test_a_figure_LAGGING_its_own_file_solves_at_the_EARLIER_day(self):
        """The 2026-08-16 shape, in miniature.

        The header states a balance the file's own tail has already moved past
        -- 1060.00 is the 03-02 cumulative, not the 03-03 one.  Nothing is
        wrong with the file; the bank computed the figure before it ledgered
        the last day.
        """
        lines = _plain(["100.00", "-40.00", "25.00"])

        assert solve_effective_day(
            lines, Decimal("1060.00"), Decimal("1000.00"), date(2026, 3, 9),
        ) == date(2026, 3, 2)

    def test_a_figure_equal_to_the_opening_solves_BEFORE_the_first_line(self):
        """The day before the first line is a candidate in its own right."""
        lines = _plain(["100.00", "-40.00"])

        assert solve_effective_day(
            lines, Decimal("1000.00"), Decimal("1000.00"), date(2026, 3, 9),
        ) == date(2026, 2, 28)

    def test_a_figure_no_day_reaches_solves_NOWHERE(self):
        """The range-export shape: the answer is None, not a nearest day."""
        lines = _plain(["100.00", "-40.00", "25.00"])

        assert solve_effective_day(
            lines, Decimal("2459.60"), Decimal("1000.00"), date(2026, 8, 23),
        ) is None

    def test_two_solving_days_take_the_LATER_one(self):
        """Harmless, and PROVEN so rather than assumed.

        Two satisfying days differ by lines summing to exactly zero, so every
        balance the app later derives is identical either way; taking the later
        is a choice about which day to NAME.  Here 03-02 and 03-03 both satisfy
        it because -40.00 and +40.00 cancel between them.
        """
        lines = _plain(["100.00", "-40.00", "40.00"])

        assert solve_effective_day(
            lines, Decimal("1100.00"), Decimal("1000.00"), date(2026, 3, 9),
        ) == date(2026, 3, 3)

    def test_no_candidate_may_be_AFTER_the_day_the_header_names(self):
        """A bank cannot state a balance for a day it has not reached.

        **The bound is what stops a 500**: without it the solve returned 03-03
        under a header dated 03-02, and
        ``ck_statement_imports_effective_day_within_file`` refused the row at
        flush -- so a file the app could describe exactly became "Something
        went wrong saving this statement".  Found by two independent
        adversarial reviews, 2026-08-23.

        Here 1085.00 solves at 03-03 and at no earlier day, so bounding at
        03-02 must yield nothing rather than the later day.
        """
        lines = _plain(["100.00", "-40.00", "25.00"])

        assert solve_effective_day(
            lines, Decimal("1085.00"), Decimal("1000.00"), date(2026, 3, 2),
        ) is None

    def test_the_bound_also_covers_the_day_BEFORE_the_first_line(self):
        """The extra candidate is bounded too, or it escapes the same check."""
        lines = _plain(["100.00"])

        assert solve_effective_day(
            lines, Decimal("1000.00"), Decimal("1000.00"), date(2026, 2, 27),
        ) is None


class TestTheEvidenceIsTheWeakestLinkInTheChain:
    """A solved day is only as good as the opening it was solved against."""

    def test_a_running_balance_PROVES_it_from_the_file_itself(self):
        """``file_chain``: nothing outside the file is consulted."""
        lines = _chain("1000.00", ["100.00", "-40.00"])

        balance = resolve_anchor(
            lines, Decimal("1060.00"), date(2026, 3, 5), None,
        )

        assert balance.evidence is _FILE_CHAIN
        assert balance.effective_on == date(2026, 3, 2)
        assert balance.is_anchored

    def test_a_recorded_opening_CORROBORATES_it(self):
        """Two statements agreeing, when the one behind it is itself proved."""
        lines = _plain(["100.00", "-40.00"])

        balance = resolve_anchor(
            lines, Decimal("1060.00"), date(2026, 3, 5),
            _known("1000.00", _CORROBORATED),
        )

        assert balance.evidence is _CORROBORATED
        assert balance.effective_on == date(2026, 3, 2)

    def test_solving_against_an_UNCORROBORATED_opening_stays_uncorroborated(
        self,
    ):
        """The whole point of the ladder, and the defect it removes.

        **Reproduced in two clicks by adversarial review, 2026-08-23**:
        re-uploading the identical file made the app walk back to its own
        assumption, find that the file agreed with it, and record the result as
        corroborated -- the assumption checking itself.  The DAY here is
        genuinely solved; the opening behind it was not, so the answer is not.
        """
        lines = _plain(["100.00", "-40.00"])

        balance = resolve_anchor(
            lines, Decimal("1060.00"), date(2026, 3, 5),
            _known("1000.00", _UNCORROBORATED),
        )

        assert balance.effective_on == date(2026, 3, 2)
        assert balance.evidence is _UNCORROBORATED

    def test_with_NOTHING_to_check_against_it_is_UNCORROBORATED(self):
        """A FIRST import, and the receipt says so."""
        lines = _plain(["100.00", "-40.00"])

        balance = resolve_anchor(
            lines, Decimal("9999.99"), date(2026, 3, 5), None,
        )

        assert balance.evidence is _UNCORROBORATED
        assert balance.effective_on == date(2026, 3, 2)

    def test_the_assumed_day_is_BOUNDED_by_the_day_the_header_names(self):
        """The other arm the CHECK constraint refused, and it had no guard.

        ``effective_on`` was ``lines[-1].posted_on`` with no comparison to the
        header's day at all, so a file listing a line past its own header --
        a bank showing a future-dated item -- raised ``IntegrityError`` inside
        the door's flush and reached the owner as a 500.  Found by two
        independent adversarial reviews, 2026-08-23.
        """
        lines = _plain(["100.00", "-40.00", "25.00"])

        balance = resolve_anchor(
            lines, Decimal("2000.00"), date(2026, 3, 1), None,
        )

        assert balance.effective_on == date(2026, 3, 1)
        assert balance.effective_on <= balance.stated_on

    def test_the_CHAIN_wins_where_both_openings_are_known(self):
        """It needs nothing outside the file, so it is the one used.

        The recorded opening here would solve at a DIFFERENT day, so the
        precedence is observable rather than a tie-break over one answer.
        """
        lines = _chain("1000.00", ["100.00", "-40.00"])

        balance = resolve_anchor(
            lines, Decimal("1060.00"), date(2026, 3, 5), _known("1100.00"),
        )

        assert balance.evidence is _FILE_CHAIN
        assert balance.effective_on == date(2026, 3, 2)

    def test_a_file_stating_no_balance_determines_nothing(self):
        """No claim, so no anchor and no refusal."""
        assert resolve_anchor(
            _plain(["100.00"]), None, None, _known("1000.00"),
        ) is None

    def test_a_figure_with_no_DAY_determines_nothing_either(self):
        """Total over its inputs rather than resting on the adapter's pairing.

        The adapter reads the two as one fact, so this is unreachable through
        the door -- and a function whose whole job is to be total may not rest
        a guard on another module's invariant.  Without it the arms would
        ``TypeError`` on the bound comparison.  Found by adversarial review
        2026-08-23.
        """
        assert resolve_anchor(
            _plain(["100.00"]), Decimal("500.00"), None, _known("1000.00"),
        ) is None


class TestTheWeakestLinkRule:
    """One comparison, stated once, over the enum's own ladder."""

    def test_it_returns_the_weaker_of_two_levels(self):
        """Both orders, so an argument swap cannot pass."""
        assert weaker_of(_FILE_CHAIN, _UNCORROBORATED) is _UNCORROBORATED
        assert weaker_of(_UNCORROBORATED, _FILE_CHAIN) is _UNCORROBORATED
        assert weaker_of(_FILE_CHAIN, _CORROBORATED) is _CORROBORATED
        assert weaker_of(_CORROBORATED, _FILE_CHAIN) is _CORROBORATED

    def test_equal_levels_return_that_level(self):
        """The identity case, which the minimum must not disturb."""
        assert weaker_of(_CORROBORATED, _CORROBORATED) is _CORROBORATED

    def test_the_ladder_is_strictly_ordered(self):
        """Proved by the file beats corroborated beats nothing.

        Asserted here rather than trusted because ``usable_anchor`` picks the
        strongest anchor by this order, and an early draft read the order off
        the ref table's row ids instead -- which was measured BACKWARDS, since
        the seed writes ``file_chain`` first.
        """
        assert _UNCORROBORATED.strength < _CORROBORATED.strength
        assert _CORROBORATED.strength < _FILE_CHAIN.strength


class TestAFileMayStateABalanceItsOwnLinesCannotReach:
    """The RANGE export, which a refusal would have rejected."""

    def test_a_header_the_lines_cannot_reach_records_the_claim_and_no_anchor(
        self,
    ):
        """The claim is real; the placement is undeterminable, and says so."""
        lines = _plain(["100.00", "-40.00"])

        balance = resolve_anchor(
            lines, Decimal("2459.60"), date(2026, 8, 23), _known("1000.00"),
        )

        assert balance.stated == Decimal("2459.60")
        assert balance.stated_on == date(2026, 8, 23)
        assert balance.effective_on is None
        assert balance.evidence is None
        assert not balance.is_anchored

    def test_a_mismatch_against_RECORDED_history_is_never_the_files_fault(
        self,
    ):
        """No refusal on this arm, whatever the header's day.

        **An earlier draft refused here** when the header sat on or before the
        file's last line, and an adversarial review reproduced it rejecting an
        honest export and telling the owner to delete an import -- blaming the
        file for the app's own stale anchor.  Only the file's own CHAIN can
        convict the file.
        """
        lines = _plain(["100.00", "-40.00"])

        balance = resolve_anchor(
            lines, Decimal("9999.99"), date(2026, 3, 1), _known("1000.00"),
        )

        assert not balance.is_anchored


class TestAFileThatCONTRADICTSItselfIsRefused:
    """The firing control, and the ONLY refusal: the chain against the header.

    A per-line running balance states what the account held on every day the
    file covers, so a header figure the chain reaches on no such day is the
    file disagreeing with itself -- which needs no evidence from outside it and
    is the one disagreement a re-export can be expected to fix.
    """

    def test_a_chain_that_reaches_the_header_on_no_day_is_refused(self):
        """The whole arm."""
        lines = _chain("1000.00", ["100.00", "-40.00"])

        with pytest.raises(StatementBalanceUnexplained) as raised:
            resolve_anchor(lines, Decimal("9999.99"), date(2026, 3, 9), None)

        assert raised.value.stated == Decimal("9999.99")
        assert raised.value.implied == Decimal("1060.00")

    def test_the_refusal_carries_BOTH_figures(self):
        """The pair is what says the file disagrees with ITSELF."""
        lines = _chain("1000.00", ["100.00"])

        with pytest.raises(StatementBalanceUnexplained) as raised:
            resolve_anchor(lines, Decimal("500.00"), date(2026, 3, 5), None)

        assert raised.value.stated == Decimal("500.00")
        assert raised.value.implied == Decimal("1100.00")
        assert "500.00" in str(raised.value)
        assert "1100.00" in str(raised.value)

    def test_a_chained_file_whose_header_merely_LAGS_is_NOT_refused(self):
        """The complement, or the arm above passes for the wrong reason.

        A header the chain DOES reach, just not on the last day, is an
        ordinary file -- the 2026-08-16 shape.
        """
        lines = _chain("1000.00", ["100.00", "-40.00"])

        balance = resolve_anchor(
            lines, Decimal("1100.00"), date(2026, 3, 9), None,
        )

        assert balance.effective_on == date(2026, 3, 1)
        assert balance.evidence is _FILE_CHAIN


class TestTheRecordedHistoryWalk:
    """What this account's OWN recorded statements say it held before a day."""

    def test_it_answers_NOTHING_for_an_account_with_no_anchored_import(
        self, app, db, seed_user,
    ):
        """A first import has nothing to check against, and says so."""
        assert recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 1),
        ) is None

    def test_an_UNANCHORED_import_is_never_selected_as_the_anchor(
        self, app, db, seed_user,
    ):
        """The filter that stops a date-range export becoming the anchor.

        Without it the anchor selection returns a row whose
        ``balance_effective_on`` is ``None`` and the coverage test raises
        ``TypeError`` comparing a date against it -- a 500.  Found by
        adversarial review 2026-08-23.  (The selection moved to
        ``_balance.usable_anchor`` at plan step ``bank_import:X-f6e-2``;
        this reaches it through the public reader that consumes it.)
        """
        _seed_import(
            db, seed_user["account"], stated="2459.60", effective_on=None,
            evidence=None, lines=[(date(2026, 3, 1), "100.00")],
        )

        assert recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 5),
        ) is None

    def test_it_walks_FORWARD_from_an_anchor_over_the_recorded_lines(
        self, app, db, seed_user,
    ):
        """Everything through the anchor's day is already inside its figure.

        Anchor: 1085.00 at 03-03 over +100.00, -40.00, +25.00.  Asked for the
        balance before 03-04, the two sums cancel and the answer is the anchor
        itself.
        """
        _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 2), "-40.00"),
                   (date(2026, 3, 3), "25.00")],
        )

        known = recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 4),
        )

        assert known.amount == Decimal("1085.00")

    def test_it_walks_BACKWARD_with_no_branch_deciding_the_sign(
        self, app, db, seed_user,
    ):
        """A day BEFORE the anchor subtracts, out of the same subtraction.

        Before 03-02: 1085.00 less the 03-02 (-40.00) and 03-03 (+25.00) lines
        is 1100.00.  Before the first line at all: less +100.00 too, so
        1000.00.
        """
        _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 2), "-40.00"),
                   (date(2026, 3, 3), "25.00")],
        )
        account_id = seed_user["account"].id

        assert recorded_opening_before(
            account_id, date(2026, 3, 2),
        ).amount == Decimal("1100.00")
        assert recorded_opening_before(
            account_id, date(2026, 3, 1),
        ).amount == Decimal("1000.00")

    def test_the_evidence_is_CAPPED_at_corroborated(self, app, db, seed_user):
        """Agreeing with a proved anchor is corroboration, not proof.

        The file being imported states no chain of its own, so it cannot
        inherit ``file_chain`` however strong the anchor behind it is.
        """
        _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
        )

        known = recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 2),
        )

        assert known.evidence is _CORROBORATED

    def test_the_evidence_is_no_STRONGER_than_the_anchor_behind_it(
        self, app, db, seed_user,
    ):
        """The weakest link, at the database tier rather than in a unit."""
        _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 1), evidence=_UNCORROBORATED,
            lines=[(date(2026, 3, 1), "100.00")],
        )

        known = recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 2),
        )

        assert known.evidence is _UNCORROBORATED

    def test_the_STRONGEST_anchor_is_chosen_not_the_most_recent(
        self, app, db, seed_user,
    ):
        """Recency is only the tie-break, and the comment saying otherwise was
        refuted.

        Two anchors on one account: a proved one at 03-01 and a later
        uncorroborated one at 03-02.  ``created_at`` is the IMPORT ACT's time,
        so ordering by it would take the weaker.  Walking from the proved
        anchor gives 1100.00 and corroboration; from the weaker one it would
        give 1150.00 and no corroboration.
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1100.00", effective_on=date(2026, 3, 1),
            evidence=_FILE_CHAIN, lines=[(date(2026, 3, 1), "100.00")],
            file_name="proved.csv",
        )
        _seed_import(
            db, account, stated="1150.00", effective_on=date(2026, 3, 2),
            evidence=_UNCORROBORATED, lines=[(date(2026, 3, 2), "50.00")],
            file_name="assumed.csv",
        )

        known = recorded_opening_before(account.id, date(2026, 3, 2))

        assert known.amount == Decimal("1100.00")
        assert known.evidence is _CORROBORATED

    def test_a_GAP_in_coverage_answers_NOTHING_rather_than_a_wrong_figure(
        self, app, db, seed_user,
    ):
        """ONE uncovered day is enough, which is the boundary that had no test.

        The anchor's import spans 03-01..03-03; 03-04 is covered by nothing, so
        a walk to 03-05 crosses a day whose lines nobody has imported.
        Answering ``None`` sends the caller to ``uncorroborated``, which the
        receipt SAYS.  A pre-existing test asked this three MONTHS past the
        boundary and so could not see an off-by-one; mutating the comparison
        to ``>=`` left the whole suite green.  Found by adversarial review
        2026-08-23.
        """
        _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 3), "0.01")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )

        assert recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 5),
        ) is None

    def test_the_day_IMMEDIATELY_after_the_span_is_still_covered(
        self, app, db, seed_user,
    ):
        """The other side of that boundary, or the arm above passes vacuously.

        Asked for the balance before 03-04, the days strictly between the
        anchor (03-03) and it are EMPTY, so nothing can be missing.
        """
        _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 3), "0.01")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )

        assert recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 4),
        ) is not None

    def test_it_sums_only_ITS_OWN_accounts_lines(
        self, app, db, seed_user, seed_second_user,
    ):
        """A real second account holding its own anchor and its own lines.

        **The previous version of this test could not fail.**  It asked for a
        NON-EXISTENT account id, so the anchor query returned ``None`` and
        execution never reached either sum -- deleting the account filter from
        both left the whole suite green while the walk summed every account's
        lines in the database into one owner's balance.  Found by adversarial
        review 2026-08-23.
        """
        _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
        )
        _seed_import(
            db, seed_second_user["account"], stated="9000.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "8000.00")],
            file_name="other.csv",
        )

        known = recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 2),
        )

        # 1100.00, not 1100.00 plus the other account's 8000.00 line.
        assert known.amount == Decimal("1100.00")


class TestTheDoorsThatChangeLinesReleaseTheAnchorsTheyUndercut:
    """An anchor is a conclusion drawn from lines at or before its own day.

    Two defects were reproduced as SILENTLY WRONG OPENINGS by independent
    adversarial reviews on 2026-08-23 -- a later export inserting a line into a
    day an earlier anchor had priced, and a delete removing the lines an anchor
    rested on -- and one rule closes both: the evidence moved, so the
    conclusion goes.
    """

    def test_it_releases_an_anchor_at_or_after_the_changed_day(
        self, app, db, seed_user,
    ):
        """A line recorded inside an anchor's window was not in its solve."""
        row = _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )

        assert release_anchors_from(
            seed_user["account"].id, date(2026, 3, 2),
        ) == 1
        db.session.flush()
        assert row.balance_effective_on is None
        assert row.balance_evidence_id is None

    def test_it_LEAVES_an_anchor_that_predates_the_change(
        self, app, db, seed_user,
    ):
        """A line after an anchor's day says nothing about days before it."""
        row = _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )

        assert release_anchors_from(
            seed_user["account"].id, date(2026, 3, 2),
        ) == 0
        assert row.balance_effective_on == date(2026, 3, 1)

    def test_the_recording_import_is_EXCLUDED_from_its_own_release(
        self, app, db, seed_user,
    ):
        """It solved against its own COMPLETE line list, so nothing undercuts it."""
        row = _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )

        assert release_anchors_from(
            seed_user["account"].id, date(2026, 3, 2),
            except_import_id=row.id,
        ) == 0
        assert row.balance_effective_on == date(2026, 3, 3)

    def test_it_is_scoped_to_ITS_OWN_account(
        self, app, db, seed_user, seed_second_user,
    ):
        """Another owner's anchors are not this owner's to release."""
        other = _seed_import(
            db, seed_second_user["account"], stated="9000.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "8000.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
            file_name="other.csv",
        )

        assert release_anchors_from(
            seed_user["account"].id, date(2026, 3, 1),
        ) == 0
        assert other.balance_effective_on == date(2026, 3, 3)
