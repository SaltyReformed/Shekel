"""
Shekel Budget App -- The app's books beside the BANK's own record.

Plan step **bank_import:X-f6e-2**, ruling **R-GF**.  The comparison is an
INSTRUMENT and never a gate, so nothing here refuses anything; what these tests
pin is that it SEES what it is for.

**The load-bearing case is ``TestATrueUpCannotHideADisagreement``**, and it is
the measurement the whole design turns on.  On the developer's own Checking
account, over the 149 days his statement and his records both cover, 35 days
carry a real disagreement between his rows and the bank's lines -- and ELEVEN of
them read as EXACT agreement in the balance difference, because a same-day
balance assertion cancels the error to the cent.  Among the eleven:

* 2026-06-02, ``-$943.41`` -- six ``CC Payback`` rows against a day his bank
  posted no line at all.  That is finding **N-337**, which this step is
  specified to detect, on a day whose two running balances are identical.
* 2026-07-31, ``-$2,090.47``; 2026-08-18, ``-$1,463.04``.
* 2026-05-21 and 2026-06-04, ``-$0.05`` each -- the payroll residue finding
  **N-239** measures.

A report built on the balance difference alone prints "agrees" on every one of
those days.  So the residue is what ``agrees`` is tested on, and this suite
asserts a day whose gap is ZERO and whose residue is not is reported as a
disagreement.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    StatementBalanceEvidenceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.models.statement_import import BankStatementLine
from app.models.statement_match import (
    StatementMatch,
    StatementMatchMember,
)
from app.services import (
    bank_agreement,
    cash_ledger,
    transaction_service,
)
from app.services.balance_at import BalanceContext
from app.services.scenario_resolver import get_baseline_scenario
from tests._test_helpers import (
    add_entry,
    an_entered_day,
    append_balance_assertion,
    generate_row_of,
    make_expense_template,
    make_income_template,
    one_off_row_of,
    settle_day_columns,
    cover_bare_settled_row,
    settlement_columns,
)
from tests.test_services.test_cash_fold import _instant
from tests.test_services.test_statement_import.test_anchor import _seed_import

_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN
_UNCORROBORATED = StatementBalanceEvidenceEnum.UNCORROBORATED
_ZERO = Decimal("0.00")


def _settled(db, seed_user, period, name, amount, day, *, is_income=False):
    """Insert one SETTLED row whose cash moved on *day*."""
    status_id = ref_cache.status_id(StatusEnum.DONE)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(str(amount)),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE,
        ),
    )
    txn.status_id = status_id
    # The settle day and record laid on BARE, as ``add_txn`` lays them: one
    # fact resolved by the shared helper, not restated (X-f1 / X-au-c3).
    for _column, _value in settlement_columns(day, amount, amount).items():
        setattr(txn, _column, _value)
    for _column, _value in settle_day_columns(day).items():
        setattr(txn, _column, _value)
    db.session.flush()
    cover_bare_settled_row(db.session, txn, amount, amount)
    return txn


def _agreement(seed_user):
    """Return the comparison for the seed user's account."""
    return bank_agreement.bank_agreement(
        seed_user["account"], BalanceContext.build(seed_user["user"].id),
    )


def _day(agreement, on):
    """Return the one :class:`AgreementDay` for *on*."""
    return next(row for row in agreement.days if row.day == on)


class TestThereIsNothingToCompareWithoutAStatement:
    """An absence, not an empty comparison."""

    def test_an_account_with_no_recorded_line_answers_NOTHING(
        self, app, seed_user, seed_periods, db,
    ):
        """No outside record exists, so there is no comparison to draw."""
        with app.app_context():
            assert _agreement(seed_user) is None


class TestATrueUpCannotHideADisagreement:
    """The measurement the design turns on."""

    def test_a_day_whose_gap_is_ZERO_still_reports_its_residue(
        self, app, seed_user, seed_periods, db,
    ):
        """The 2026-06-02 shape: real money missing, balances identical.

        The app records ``-100.00`` the bank never posted, and the owner trues
        the balance up by ``+100.00`` the same day.  Both running balances land
        on the same figure, so a report built on the difference of two balances
        prints ``$0.00`` and calls it agreement -- while ``$100.00`` of spending
        exists on exactly one of the two records.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 4), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 2), "50.00"),
                       (date(2026, 3, 4), "-25.00")],
            )
            _settled(
                db, seed_user, seed_periods[4], "Card payback", "100.00",
                date(2026, 3, 3),
            )
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1234.00"), _instant(2026, 3, 3),
            )
            db.session.commit()

            row = _day(_agreement(seed_user), date(2026, 3, 3))

            # The instrument sees it...
            assert row.recorded == Decimal("-100.00")
            assert row.bank_lines == _ZERO
            assert row.residue == Decimal("-100.00")
            assert row.agrees is False
            # ...and the true-up is NAMED rather than folded into the movement.
            assert row.asserted != _ZERO

    def test_agrees_is_decided_on_the_RESIDUE_and_never_on_the_gap(
        self, app, seed_user, seed_periods, db,
    ):
        """A FIRING control over the one predicate the design replaced.

        Testing ``agrees`` on the gap instead leaves this the failing case: the
        two balances are engineered equal on the very day the records differ.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 4), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 2), "50.00"),
                       (date(2026, 3, 4), "-25.00")],
            )
            _settled(
                db, seed_user, seed_periods[4], "Card payback", "100.00",
                date(2026, 3, 3),
            )
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1025.00"), _instant(2026, 3, 3),
            )
            db.session.commit()

            row = _day(_agreement(seed_user), date(2026, 3, 3))

            assert row.gap == _ZERO
            assert row.residue != _ZERO
            assert row.agrees is False


class TestTheResidueIsWhatTheTwoRecordsDisagreeAbout:
    """What each side has that the other does not."""

    def test_a_line_the_app_never_recorded_shows_as_a_residue(
        self, app, seed_user, seed_periods, db,
    ):
        """The bank moved money the app has no row for, BOTH ways.

        A ``-60.00`` debit nobody entered leaves the app's total ``+60.00``
        AHEAD; a ``+10.00`` deposit nobody entered leaves it ``-10.00``
        behind.  Both are "the bank recorded something the app did not", and
        they land in OPPOSITE totals -- which is why those totals are named for
        which balance ends higher rather than for whose record is missing an
        item.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 2), "-60.00"),
                       (date(2026, 3, 3), "10.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert _day(agreement, date(2026, 3, 2)).residue == Decimal("60.00")
            assert _day(agreement, date(2026, 3, 3)).residue == Decimal("-10.00")
            assert agreement.app_ahead == Decimal("60.00")
            assert agreement.bank_ahead == Decimal("10.00")

    def test_a_matching_day_agrees_and_is_not_counted(
        self, app, seed_user, seed_periods, db,
    ):
        """Both records say the same thing, so nothing is reported."""
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            _settled(
                db, seed_user, seed_periods[4], "Groceries", "40.00",
                date(2026, 3, 3),
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert _day(agreement, date(2026, 3, 3)).agrees is True
            assert agreement.disagreeing == []


class TestTheSpanStopsAtTheReadersNow:
    """Past today the app's balance is a PLAN, and a plan is not a record."""

    def test_a_line_dated_beyond_today_is_not_compared(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling R-G clamps a still-Projected row to ``as_of + 1``.

        So every day past the reader's NOW carries plan, and putting a plan
        beside a bank line would report agreement where the app has merely
        predicted what the bank did -- or a disagreement about money nobody has
        spent yet.  A FIRING control: without the bound the span runs to the
        last recorded line and those days are compared.
        """
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            beyond = ctx.as_of + timedelta(days=7)
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=ctx.as_of, evidence=_FILE_CHAIN,
                lines=[(ctx.as_of, "-10.00"), (beyond, "-99.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.span.last_day == ctx.as_of
            assert all(row.day <= ctx.as_of for row in agreement.days)

    def test_a_statement_ENTIRELY_in_the_future_is_not_reported_as_ABSENT(
        self, app, seed_user, seed_periods, db,
    ):
        """The lines exist; none of them is comparable yet.

        **A reproduced defect in this step's own first build.** Answering
        ``None`` here made the page say "this account has no recorded bank
        lines" over a file full of them -- a false statement about the owner's
        own data.  Nothing in the importer refuses a future-dated line, so the
        shape is reachable rather than theoretical.
        """
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            beyond = ctx.as_of + timedelta(days=5)
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=beyond, evidence=_FILE_CHAIN,
                lines=[(beyond, "-99.00"),
                       (beyond + timedelta(days=1), "-1.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement is not None
            assert agreement.days == []
            assert agreement.span.recorded_through == beyond + timedelta(days=1)
            assert agreement.compared == []
            assert agreement.constant_offset is None

    def test_the_truncation_is_REPORTED_and_never_silent(
        self, app, seed_user, seed_periods, db,
    ):
        """A span stopping short of what is recorded must say so.

        A report of part of the record, presented as a report of the record,
        understates every total on it -- and this page's whole purpose is to be
        the control that other leaves are checked against.
        """
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            beyond = ctx.as_of + timedelta(days=7)
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=ctx.as_of, evidence=_FILE_CHAIN,
                lines=[(ctx.as_of, "-10.00"), (beyond, "-99.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.span.recorded_through == beyond
            assert agreement.span.ends_early

    def test_an_ordinary_span_reports_no_truncation(
        self, app, seed_user, seed_periods, db,
    ):
        """The common case: every recorded line is inside the comparison."""
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert not agreement.span.ends_early


class TestDaysBeforeTheRecordsBeginAreLabelledNotCounted:
    """Finding N-314 is the balance arc's, not this report's."""

    def test_a_day_before_the_first_cash_fact_is_out_of_the_totals(
        self, app, seed_user, seed_periods, db,
    ):
        """The developer's own shape: 83 days of statement before any record.

        The bank's line is SHOWN -- hiding a day the bank describes would be
        the report deciding what may be seen -- and excluded from the counts,
        because the app holding nothing that far back is not a disagreement
        this arc can act on.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            before = walk.anchor_facts[0].observed_on - timedelta(days=5)
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=before, evidence=_FILE_CHAIN,
                lines=[(before, "-77.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)
            row = _day(agreement, before)

            assert row.in_records is False
            assert row.bank_lines == Decimal("-77.00")
            assert agreement.compared == []
            assert agreement.disagreeing == []
            assert agreement.app_ahead == _ZERO


    def test_a_statement_ENTIRELY_before_the_records_compares_nothing(
        self, app, seed_user, seed_periods, db,
    ):
        """Every day is outside the records, so every total is zero.

        The far edge of the developer's own shape: his statement reaches 83
        days before his records, and a statement reaching back further still
        would reach before ALL of them.  The report must then say it compared
        nothing rather than report the whole span as disagreement -- which is
        the difference between "your books are wrong by $15,000" and "your
        books do not go back this far".
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            begins = walk.anchor_facts[0].observed_on
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=begins - timedelta(days=10),
                evidence=_FILE_CHAIN,
                lines=[(begins - timedelta(days=20), "-500.00"),
                       (begins - timedelta(days=10), "-77.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.days != []
            assert agreement.compared == []
            assert agreement.disagreeing == []
            assert agreement.app_ahead == _ZERO
            assert agreement.bank_ahead == _ZERO
            assert agreement.asserted_total == _ZERO
            assert agreement.constant_offset is None


class TestWhichSideRestsOnAnAssumption:
    """Ruling R-GF, finding N-342."""

    def test_no_anchor_means_no_bank_balance_and_no_constant_offset(
        self, app, seed_user, seed_periods, db,
    ):
        """The state BOTH of the developer's real imports are in.

        The movement half still answers -- it needs no anchor -- which is why
        the report is worth rendering at all on an account whose files state no
        balance.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated=None,
                lines=[(date(2026, 3, 2), "-60.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)
            row = _day(agreement, date(2026, 3, 2))

            assert not agreement.anchored
            assert [run.anchor for run in agreement.runs] == [None]
            assert row.bank_balance is None
            assert row.gap is None
            assert agreement.constant_offset is None
            assert row.residue == Decimal("60.00")

    def test_a_CONSTANT_offset_is_reported_when_every_movement_agrees(
        self, app, seed_user, seed_periods, db,
    ):
        """A level wrong by K shifts every day equally; movements stay right.

        **Two days that both move, not one.**  Its first form seeded a single
        day, where "every gap is equal" is satisfied vacuously and the
        assertion could not distinguish a constant offset from there being
        nothing to be constant across -- and the population that hits that is
        exactly N-342's, an owner importing a statement the week they start
        recording.  Measured by adversarial review 2026-08-24.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            opening = walk.anchor_facts[0].observed_on
            first, second = (
                opening + timedelta(days=1), opening + timedelta(days=2),
            )
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=second, evidence=_UNCORROBORATED,
                lines=[(first, "-40.00"), (second, "-10.00")],
            )
            _settled(
                db, seed_user, seed_periods[0], "Groceries", "40.00", first,
            )
            _settled(
                db, seed_user, seed_periods[0], "Coffee", "10.00", second,
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert len(agreement.compared) >= 2
            assert agreement.disagreeing == []
            assert agreement.constant_offset is not None
            assert agreement.constant_offset != _ZERO
            (run,) = agreement.runs
            assert run.anchor.evidence is _UNCORROBORATED

    def test_ONE_compared_day_cannot_demonstrate_a_constant_offset(
        self, app, seed_user, seed_periods, db,
    ):
        """A FIRING control over the vacuous case.

        One day satisfies "every gap is equal" whatever the gap is, so
        reporting it as the signature of a wrong starting figure is an
        inference from a single observation -- rendered on screen as *"Every
        compared day is out by exactly $X"*.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            only = walk.anchor_facts[0].observed_on + timedelta(days=1)
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=only, evidence=_UNCORROBORATED,
                lines=[(only, "-40.00")],
            )
            _settled(
                db, seed_user, seed_periods[0], "Groceries", "40.00", only,
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert len(agreement.compared) == 1
            assert agreement.constant_offset is None


class TestThePageCannotClaimAWalkItDidNotPerform:
    """A derivation named over a column of dashes."""

    def test_days_in_the_GAP_between_two_anchored_runs_are_counted(
        self, app, seed_user, seed_periods, db,
    ):
        """Reproduced by adversarial review 2026-08-24; half of it since fixed.

        A ``file_chain`` anchor in an old run was the STRONGEST, so it was
        the one walked from for the whole account -- and it reached nothing
        in a later, disconnected run, including that run's own anchored day
        whose crossing is empty (finding **N-343**).  Since plan step
        ``balance:X-bj-1b`` (ruling **R-BAL63**) each run walks from its own
        anchor, so the March day is PRICED -- the developer flipped this
        assertion with that ruling -- and what remains unpriced is the gap
        between the runs, which the report must still count rather than name
        a figure over dashes.  The day BEFORE the March run's first line is
        reached from the March anchor, so the gap counts 02-01..03-13.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 1, 31), evidence=_FILE_CHAIN,
                lines=[(date(2026, 1, 31), "10.00")],
                period=(date(2026, 1, 1), date(2026, 1, 31)),
            )
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=date(2026, 3, 20), evidence=_UNCORROBORATED,
                file_name="march.csv",
                lines=[(date(2026, 3, 20), "-5.00")],
                period=(date(2026, 3, 15), date(2026, 3, 20)),
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.anchored
            assert [run.anchor.day for run in agreement.runs] == [
                date(2026, 1, 31), date(2026, 3, 20),
            ]
            # 02-01..03-13 inclusive: 28 February days and 13 March days.
            assert agreement.unpriced_days == 41
            assert _day(agreement, date(2026, 3, 13)).bank_balance is None
            # The day before the March run's first line is the balance its
            # lines start from, walked back from the March anchor.
            assert _day(agreement, date(2026, 3, 14)).bank_balance == Decimal(
                "505.00",
            )
            # The later run's own anchored day, priced from its own anchor.
            assert _day(agreement, date(2026, 3, 20)).bank_balance == Decimal(
                "500.00",
            )

    def test_an_account_with_NO_anchor_reports_zero_unpriced_days(
        self, app, seed_user, seed_periods, db,
    ):
        """One absence must not be reported as two.

        With no anchor the page already says no statement places a balance;
        adding "232 days not priced" beside it would name the same fact twice
        and read as a second, separate problem.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated=None,
                lines=[(date(2026, 3, 2), "-60.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert not agreement.anchored
            assert agreement.unpriced_days == 0


class TestTheReportCarriesTheRuns:
    """Plan step ``balance:X-bj-1b``: the runs are the ONE home of coverage.

    Ruling **R-BAL65**: ``BankAgreement.runs`` carries every recorded run
    with its anchor and checkpoints, ``imports`` is derived from it, and the
    per-day bank balances are each run's own walk.
    """

    def test_runs_carry_each_anchor_and_imports_is_DERIVED_from_them(
        self, app, seed_user, seed_periods, db,
    ):
        """Two anchored runs: two anchors, two spans, and one source of both.

        The hero (``headline``) walks back to the latest priced compared day,
        which is now the later run's own anchored day rather than the older
        run's, and the standing gap on the span's last day is a figure.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 1, 31), evidence=_FILE_CHAIN,
                lines=[(date(2026, 1, 31), "10.00")],
                period=(date(2026, 1, 1), date(2026, 1, 31)),
                file_name="january.csv",
            )
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=date(2026, 3, 20), evidence=_UNCORROBORATED,
                file_name="march.csv",
                lines=[(date(2026, 3, 15), "50.00"),
                       (date(2026, 3, 20), "-5.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.imports == [
                (run.first_day, run.last_day) for run in agreement.runs
            ] == [
                (date(2026, 1, 1), date(2026, 1, 31)),
                (date(2026, 3, 15), date(2026, 3, 20)),
            ]
            assert [run.anchor.file_name for run in agreement.runs] == [
                "january.csv", "march.csv",
            ]
            assert agreement.headline.day == date(2026, 3, 20)
            assert agreement.headline.bank_balance == Decimal("500.00")
            # The level on the span's last day: the books less the bank's
            # 500.00 there, a figure now rather than the ``None`` the
            # account-wide walk left on every day it could not reach.
            assert agreement.standing_gap == (
                _day(agreement, date(2026, 3, 20)).app_balance
                - Decimal("500.00")
            )

    def test_a_checkpoint_rides_on_its_run_and_moves_no_day(
        self, app, seed_user, seed_periods, db,
    ):
        """The disagreeing statement is listed; the column is the anchor's walk."""
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 5), evidence=_FILE_CHAIN,
                file_name="ytd.csv",
                lines=[(date(2026, 3, 1), "100.00"),
                       (date(2026, 3, 3), "-40.00"),
                       (date(2026, 3, 5), "25.00")],
            )
            _seed_import(
                db, seed_user["account"], stated="1075.00",
                effective_on=date(2026, 3, 3), evidence=_UNCORROBORATED,
                file_name="guessed.csv", lines=[],
                period=(date(2026, 3, 1), date(2026, 3, 3)),
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            (run,) = agreement.runs
            (checkpoint,) = run.checkpoints
            assert checkpoint.file_name == "guessed.csv"
            assert checkpoint.difference == Decimal("-100.00")
            assert _day(agreement, date(2026, 3, 3)).bank_balance == Decimal(
                "975.00",
            )

    def test_a_run_with_no_level_counts_its_days_unpriced_beside_an_anchored_one(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling **R-BAL64** on the report: the count names both causes.

        February (02-20) is anchored; March 1..5 holds lines and no figure.
        Unpriced: the gap 02-21..02-28 (8 days) and March's 5 days, 13 --
        March's day-before-first-line among the gap's, since no anchor
        reaches it either.  (The service suite's today is 2026-03-20.)
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=date(2026, 2, 20), evidence=_UNCORROBORATED,
                file_name="february.csv",
                lines=[(date(2026, 2, 20), "-5.00")],
            )
            _seed_import(
                db, seed_user["account"], stated=None, file_name="march.csv",
                lines=[(date(2026, 3, 1), "50.00"),
                       (date(2026, 3, 5), "-20.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            february, march = agreement.runs
            assert february.anchor is not None
            assert march.anchor is None
            assert agreement.anchored
            assert agreement.unpriced_days == 13
            assert _day(agreement, date(2026, 2, 28)).bank_balance is None
            assert _day(agreement, date(2026, 3, 5)).bank_balance is None


class TestTheDrillDownNamesWhatMakesUpADay:
    """A number a reader can act on, rather than one they must go and chase."""

    def test_it_lists_both_sides_with_their_match_state(
        self, app, seed_user, seed_periods, db,
    ):
        """The 2026-06-02 shape again: six app rows, no bank line.

        Every row and every line is listed rather than only the unmatched
        ones -- a row matched to a line the bank posted on a DIFFERENT day
        contributes to both days' residues while being unmatched on neither.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            _settled(
                db, seed_user, seed_periods[4], "Card payback", "100.00",
                date(2026, 3, 3),
            )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [line.amount for line in detail.lines] == [
                Decimal("-40.00"),
            ]
            assert detail.lines[0].matched is False
            assert [(row.description, row.amount) for row in detail.rows] == [
                ("Card payback", Decimal("-100.00")),
            ]
            assert detail.rows[0].matched is False

    def test_the_rows_it_lists_SUM_to_the_days_recorded_total(
        self, app, seed_user, seed_periods, db,
    ):
        """The items shown explain exactly the figure they sit under.

        Listing the account's transactions by ``settled_on`` instead of reading
        the walk would be a second statement of which rows count as settled
        cash and on what day, and a drill-down that did not add up is how a
        reader learns to stop trusting the number above it.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            for name, amount in (("A", "10.00"), ("B", "25.50"),
                                 ("C", "4.50")):
                _settled(
                    db, seed_user, seed_periods[4], name, amount,
                    date(2026, 3, 3),
                )
            # Rows on OTHER days, so the day filter is exercised.  Without
            # them every settled row in the account sat on the one day asked
            # about, and deleting ``fact.settled_on == day`` from ``_rows_on``
            # left the suite green -- the drill-down would list the whole
            # account under every day.  Found by adversarial review
            # 2026-08-24: setup that does not create the condition it names.
            _settled(
                db, seed_user, seed_periods[4], "Elsewhere", "99.00",
                date(2026, 3, 2),
            )
            _settled(
                db, seed_user, seed_periods[4], "Later", "77.00",
                date(2026, 3, 5),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            detail = bank_agreement.day_detail(
                seed_user["account"], ctx, date(2026, 3, 3),
            )
            row = _day(_agreement(seed_user), date(2026, 3, 3))

            assert sum(item.amount for item in detail.rows) == row.recorded
            assert sum(item.amount for item in detail.lines) == row.bank_lines
            assert {item.description for item in detail.rows} == {
                "A", "B", "C",
            }

    def test_a_day_with_nothing_on_it_answers_with_empty_lists(
        self, app, seed_user, seed_periods, db,
    ):
        """"Neither of us has anything" is an answer a reader asked for."""
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 9),
            )

            assert detail.lines == []
            assert detail.rows == []


class TestTheDrillDownSaysWhatIsALREADYEXPLAINED:
    """The match flag, and the envelope-purchase branch beside it."""

    def test_a_MATCHED_line_and_a_MATCHED_row_are_marked(
        self, app, seed_user, seed_periods, db,
    ):
        """Both flags, against a real accepted match.

        A FIRING control: with the flag hardcoded ``False`` the whole
        ``_claimed_app_rows`` lookup, both template branches and their copy are
        dead, and the screen tells the owner nothing is explained when
        everything is.  The axis no other test in this file varies.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            txn = _settled(
                db, seed_user, seed_periods[4], "Groceries", "40.00",
                date(2026, 3, 3),
            )
            line = db.session.query(BankStatementLine).filter(
                BankStatementLine.account_id == seed_user["account"].id,
            ).one()
            match = StatementMatch(
                account_id=seed_user["account"].id,
                user_id=seed_user["user"].id,
                applied_by_rule=False,
            )
            db.session.add(match)
            db.session.flush()
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                bank_statement_line_id=line.id,
            ))
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                transaction_id=txn.id,
            ))
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [line.matched for line in detail.lines] == [True]
            assert [row.matched for row in detail.rows] == [True]

    @pytest.mark.parametrize(
        ("is_income", "amount", "line_amount"),
        [(False, "40.00", "-40.00"), (True, "2572.78", "2572.78")],
        ids=["a covered bill", "a covered paycheck"],
    )
    def test_a_COVERED_row_matched_by_ROW_reads_matched(
        self, app, seed_user, seed_periods, db, is_income, amount, line_amount,
    ):
        """The mirror's match state is its PARENT's.

        Settled through the seam, a bill's (X-bi-3a) or a paycheck's
        (X-bi-3b) money walks as its covering movement's fact, while the
        match names the ROW -- the matcher's subject, its mirror kept out of
        the purchase candidates.  The case above builds its row around the
        seam and so never met this; with the entry's own claim asked, every
        matched bill and paycheck read as unexplained here (adversarial review
        of X-bi-3b, 2026-09-16).
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), line_amount)],
            )
            make = make_income_template if is_income else make_expense_template
            txn = generate_row_of(
                make(db.session, seed_user, amount=amount, name="Row"),
                seed_periods[4],
            )
            transaction_service.settle_transaction(
                txn, settle_day=an_entered_day(date(2026, 3, 3)),
            )
            db.session.flush()
            assert len(txn.covering_movements) == 1
            line = db.session.query(BankStatementLine).filter(
                BankStatementLine.account_id == seed_user["account"].id,
            ).one()
            match = StatementMatch(
                account_id=seed_user["account"].id,
                user_id=seed_user["user"].id,
                applied_by_rule=False,
            )
            db.session.add(match)
            db.session.flush()
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                bank_statement_line_id=line.id,
            ))
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                transaction_id=txn.id,
            ))
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [row.amount for row in detail.rows] == [Decimal(line_amount)]
            assert [row.matched for row in detail.rows] == [True]

    def test_an_ENVELOPE_PURCHASE_is_named_from_its_ENTRY(
        self, app, seed_user, seed_periods, db,
    ):
        """The two-subject split, which no other test reaches.

        A purchase is a cash fact in its own right, carrying an ``entry_id``,
        so its name comes from ``transaction_entries.description`` and its
        match state from the ENTRY rather than the parent.  With the entry
        branch dead the row renders as ``(unnamed)`` -- and that branch is
        precisely what ``_rows_on``'s docstring names as the reason the lookup
        is keyed on the pair.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            envelope = _settled(
                db, seed_user, seed_periods[4], "Groceries", "100.00",
                date(2026, 3, 3),
            )
            add_entry(
                db.session, seed_user, envelope, Decimal("31.00"),
                date(2026, 3, 3), settled_on=date(2026, 3, 3),
                description="Food Lion",
            )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            named = {row.description for row in detail.rows}
            assert "Food Lion" in named
            assert "(unnamed)" not in named

    def test_the_biggest_movement_reads_FIRST(
        self, app, seed_user, seed_periods, db,
    ):
        """The item explaining most of a day's difference leads.

        Unpinned, the ordering is whatever the walk happened to produce, and
        the drill-down's whole job is putting the explanation in front of the
        reader.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            for name, amount in (("Small", "5.00"), ("Biggest", "900.00"),
                                 ("Middle", "50.00")):
                _settled(
                    db, seed_user, seed_periods[4], name, amount,
                    date(2026, 3, 3),
                )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [row.description for row in detail.rows] == [
                "Biggest", "Middle", "Small",
            ]
