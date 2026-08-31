"""Money COMING IN that no app row explains: the door, and what it refuses.

Ruling **bank_import:R-GW**, plan step **bank_import:X-gf-1**.

**The step's own acceptance test is the shape that had NO ACT AT ALL.**  On the
developer's own dev database 2026-08-27, eight of the 27 lines
``awaiting_review_count`` reported were inflows SMALLER than `$39.54` -- the
smallest positive row the hand-build form offered -- five dividends of `$0.12`
to `$0.22` and three card refunds of `$11.73` to `$28.29`, `$58.87` together.
No single row and no SUM of positive rows could equal one, the create door
refuses an inflow by name, and a match refuses an empty side: the two refusals
pointed at each other and the badge could never reach zero.

**What the step promises, and what each class below pins:**

* an inflow becomes an ordinary row with NO CATEGORY, settled on the bank's
  day, which books to the per-owner Uncategorized ledger account;
* the row is MATCHED to the line, so the line stops being unexplained and the
  awaiting count falls;
* the row is recorded as this act's CREATION, so an undo takes it back;
* an OUTFLOW, a ZERO line, a line another match claims and a line on another
  owner's account are all refused;
* a line no SAVED pay period covers is refused BEFORE anything is written;
* no standing rule can reach this door at all.

Every refusal here is a FIRING CONTROL: written to fail if the refusal were
deleted, which is the standard ``docs/plans/verification.md`` sets.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    SettledDayBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from sqlalchemy.exc import IntegrityError

from app.exceptions import ValidationError
from app.extensions import db
from app.utils.dates import display_today
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.statement_match import (
    StatementMatchCreation,
    StatementMatchMember,
)
from app.models.transaction import Transaction
from app.services import statement_match
from app.services.statement_match import (
    Consent,
    RuleView,
    IncomeCreation,
    MatchSubmission,
    ReviewedBatch,
    apply_reviewed,
    record_income_from_line,
    review_set,
)

from ._builders import (
    a_bank_line,
    a_later_period,
    a_rule,
    a_scope,
    a_transaction,
    an_import,
)


def _a_deposit(seed_user, amount="0.15", **kwargs):
    """Stage one unexplained line of money coming IN.

    Args:
        seed_user: The seeded user bundle.
        amount: Signed, POSITIVE into the account.
        **kwargs: Passed through to :func:`a_bank_line`.

    Returns:
        The staged line.
    """
    return a_bank_line(
        seed_user, an_import(seed_user), amount=amount,
        description="DIVIDEND EARNED (Dividend Earned)",
        merchant=kwargs.pop("merchant", "Dividend Earned"), **kwargs,
    )


def _record(seed_user, line, account=None):
    """Run the door for one line, through a scope derived at the point of use.

    **The RULE VIEW is derived at the point of use too**, for the reason the
    scope is (:func:`a_scope`): the door reads it to decide what the deposit is
    filed under (**R-HT(a)**), and a stale one would answer with rules a case
    had just replaced.  ``apply_reviewed`` derives it once per batch and
    threads it; a case calling this door directly stands in for that caller.

    Args:
        seed_user: The seeded user bundle.
        line: The bank line to record.
        account: The account being reviewed, defaulting to the seeded one.

    Returns:
        The :class:`~app.services.statement_match.RecordedIncome`.
    """
    scope = a_scope(seed_user, account)
    return record_income_from_line(
        IncomeCreation(line_id=line.id), scope,
        RuleView.build(scope.owner_id, scope.account_id),
    )


def _rows(seed_user):
    """Return every transaction on the seeded account, newest id last.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The rows.
    """
    return (
        db.session.query(Transaction)
        .filter(Transaction.account_id == seed_user["account"].id)
        .order_by(Transaction.id)
        .all()
    )


class TestTheRowItWrites:
    """What a recorded deposit IS, clause by clause."""

    def test_it_is_an_INCOME_row_at_the_banks_own_figure(
        self, app, db, seed_user,
    ):
        """The direction comes from the sign and the figure from the line."""
        line = _a_deposit(seed_user, amount="0.15")

        recorded = _record(seed_user, line)

        row = db.session.get(Transaction, recorded.transaction_id)
        assert row.transaction_type_id == ref_cache.txn_type_id(
            TxnTypeEnum.INCOME,
        )
        # The MAGNITUDE is stored, because the column is non-negative by
        # ``ck_transactions_estimated_amount``; the direction is the type.
        assert row.estimated_amount == Decimal("0.15")

    def test_it_carries_NO_category(self, app, db, seed_user):
        """R-FN's clause: the app does not know what this money was."""
        line = _a_deposit(seed_user)

        recorded = _record(seed_user, line)

        assert db.session.get(
            Transaction, recorded.transaction_id,
        ).category_id is None

    def test_it_settles_on_the_BANKS_day_as_an_OBSERVED_one(
        self, app, db, seed_user,
    ):
        """A statement SHOWED the money, so the basis is not a bound."""
        day = seed_user["bootstrap_period"].start_date + timedelta(days=3)
        line = _a_deposit(seed_user, posted_on=day)

        recorded = _record(seed_user, line)

        row = db.session.get(Transaction, recorded.transaction_id)
        assert row.settled_on == day
        assert row.settled_day_basis_id == ref_cache.settled_day_basis_id(
            SettledDayBasisEnum.OBSERVED,
        )
        assert row.status.is_settled

    def test_it_OWNS_its_amount(self, app, db, seed_user):
        """It names no template, transfer or card spend, so it reads none."""
        line = _a_deposit(seed_user)

        recorded = _record(seed_user, line)

        row = db.session.get(Transaction, recorded.transaction_id)
        assert row.amount_source_id is None
        assert row.template_id is None
        assert row.transfer_id is None

    def test_it_is_placed_by_the_POSTING_day(self, app, db, seed_user):
        """The residual's rule, not the purchase's: this row IS the movement.

        Staged so the two clocks DISAGREE across a period boundary, which is
        the only shape where the choice is observable: the bank states a
        transaction day inside the bootstrap period and posts it inside the
        next one.  Reading ``transaction_on`` would file this deposit against
        the earlier paycheck.
        """
        later = a_later_period(seed_user)
        line = _a_deposit(
            seed_user,
            transaction_on=seed_user["bootstrap_period"].end_date,
            posted_on=later.start_date,
        )

        recorded = _record(seed_user, line)

        assert recorded.pay_period_id == later.id
        assert db.session.get(
            Transaction, recorded.transaction_id,
        ).pay_period_id == later.id

    def test_it_is_NAMED_for_the_merchant_not_the_whole_line(
        self, app, db, seed_user,
    ):
        """A row nobody can read is the one thing this name must not be."""
        line = _a_deposit(seed_user, merchant="Dividend Earned")

        recorded = _record(seed_user, line)

        assert db.session.get(
            Transaction, recorded.transaction_id,
        ).name == "Dividend Earned"

    def test_it_falls_back_to_the_DESCRIPTION_where_no_merchant_is_named(
        self, app, db, seed_user,
    ):
        """``transactions.name`` is NOT NULL and this door writes it directly.

        The majority case on a real statement: 179 of the developer's 361
        lines state no merchant at all.
        """
        line = _a_deposit(seed_user, merchant=None)

        recorded = _record(seed_user, line)

        assert db.session.get(
            Transaction, recorded.transaction_id,
        ).name == "DIVIDEND EARNED (Dividend Earned)"


class TestItMovesTheMoney:
    """The whole point: the books stop being under the bank."""

    def test_the_entry_it_posts_BALANCES(self, app, db, seed_user):
        """Both legs, so it moves CASH as well as filling a bucket.

        The counter leg alone would satisfy the bucket case below while
        booking nothing against the account the statement is about -- which is
        the failure that would leave the books exactly as under-stated as
        before, with a row on the grid saying otherwise.
        """
        line = _a_deposit(seed_user, amount="0.15")

        recorded = _record(seed_user, line)

        # **Named by ACCOUNT, never grouped by ``is_fallback``.**  This owner
        # holds more than one non-fallback ledger account -- the checking ASSET
        # and a ``Checking -- Opening`` EQUITY row among them -- so a grouping
        # by that flag reads the same whether the debit lands on the cash
        # account or on opening equity, and only one of those is this door
        # booking a deposit.  Measured on the developer's own dev database
        # 2026-08-27, where a hand true-up posts a second entry doing exactly
        # that reversal.
        legs = dict(
            db.session.query(
                LedgerAccount.id, db.func.sum(Posting.amount),
            )
            .join(Posting, Posting.ledger_account_id == LedgerAccount.id)
            .join(JournalEntry, JournalEntry.id == Posting.journal_entry_id)
            .filter(JournalEntry.transaction_id == recorded.transaction_id)
            .group_by(LedgerAccount.id)
            .all()
        )
        cash = (
            db.session.query(LedgerAccount.id)
            .filter(
                LedgerAccount.account_id == seed_user["account"].id,
                LedgerAccount.class_id == ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.ASSET,
                ),
            )
            .scalar()
        )
        bucket = (
            db.session.query(LedgerAccount.id)
            .filter(
                LedgerAccount.user_id == seed_user["user"].id,
                LedgerAccount.is_fallback.is_(True),
                LedgerAccount.class_id == ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.INCOME,
                ),
            )
            .scalar()
        )
        # Debit-positive: the CHECKING ASSET gains 0.15 and Uncategorized
        # Income is credited the same.  Two legs and no others.
        assert legs == {cash: Decimal("0.15"), bucket: Decimal("-0.15")}

    def test_it_books_to_the_per_owner_UNCATEGORIZED_INCOME_account(
        self, app, db, seed_user,
    ):
        """The routing a NULL category produces, which is why it is NULL.

        Asserted over the LEDGER rather than the row, because the row carrying
        no category is only half the claim: what makes the mechanism real is
        that ``posting_service`` sends such a row's counter leg to the
        per-(owner, class) fallback -- and INCOME is the class this door is the
        first writer of.
        """
        line = _a_deposit(seed_user, amount="0.15")

        _record(seed_user, line)

        account = (
            db.session.query(LedgerAccount)
            .filter(
                LedgerAccount.user_id == seed_user["user"].id,
                LedgerAccount.is_fallback.is_(True),
                LedgerAccount.class_id == ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.INCOME,
                ),
            )
            .one()
        )
        net = (
            db.session.query(db.func.sum(Posting.amount))
            .filter(Posting.ledger_account_id == account.id)
            .scalar()
        )
        assert net == Decimal("-0.15")


class TestTheLineStopsBeingUnexplained:
    """A recorded deposit is MATCHED to its own row, like every other act."""

    def test_it_records_a_match_naming_both(self, app, db, seed_user):
        """Without this the line would be re-offered on the next render."""
        line = _a_deposit(seed_user)

        recorded = _record(seed_user, line)

        members = (
            db.session.query(StatementMatchMember)
            .filter(StatementMatchMember.match_id == recorded.match_id)
            .all()
        )
        assert {m.bank_statement_line_id for m in members} == {line.id, None}
        assert {m.transaction_id for m in members} == {
            None, recorded.transaction_id,
        }

    def test_the_awaiting_count_FALLS(self, app, db, seed_user):
        """The figure the grid badge renders, measured on both sides.

        This is the whole reason the step exists: before it, no act on the
        review screen could move this number for an inflow.
        """
        line = _a_deposit(seed_user)
        opens = a_scope(seed_user).calendar.opening_bound()
        before = statement_match.awaiting_review_count(
            seed_user["account"].id, opens,
        )

        _record(seed_user, line)

        after = statement_match.awaiting_review_count(
            seed_user["account"].id, opens,
        )
        assert (before, after) == (1, 0)

    def test_it_leaves_the_recordable_list(self, app, db, seed_user):
        """The screen's own re-derivation agrees with the count."""
        line = _a_deposit(seed_user)
        assert [
            item.line.line_id
            for item in review_set(a_scope(seed_user)).recordable_inflows
        ] == [line.id]

        _record(seed_user, line)

        assert review_set(a_scope(seed_user)).recordable_inflows == ()

    def test_the_row_is_recorded_as_this_ACTS_creation(
        self, app, db, seed_user,
    ):
        """R-GG: an undo takes back what the act MADE, not what it names."""
        line = _a_deposit(seed_user)

        recorded = _record(seed_user, line)

        created = (
            db.session.query(StatementMatchCreation)
            .filter(StatementMatchCreation.match_id == recorded.match_id)
            .all()
        )
        assert [c.transaction_id for c in created] == [
            recorded.transaction_id,
        ]

    def test_UNDOING_the_match_removes_the_row_again(
        self, app, db, seed_user,
    ):
        """The inverse, end to end: the books go back to where they were."""
        line = _a_deposit(seed_user)
        recorded = _record(seed_user, line)
        db.session.commit()

        statement_match.release_match(
            seed_user["user"].id, seed_user["account"].id, recorded.match_id,
        )
        db.session.commit()

        assert db.session.get(Transaction, recorded.transaction_id) is None
        assert [
            item.line.line_id
            for item in review_set(a_scope(seed_user)).recordable_inflows
        ] == [line.id]


class TestWhatItRefuses:
    """Every arm, written to fail if its refusal were deleted."""

    def test_an_OUTFLOW_is_refused(self, app, db, seed_user):
        """The exact mirror of the create door's own inflow refusal.

        Without it a `-$180.00` debit would be booked as `+$180.00` of income:
        the writer takes the direction from the SIGN, so the refusal is what
        keeps the two doors total over the lines rather than overlapping.
        """
        line = _a_deposit(seed_user, amount="-180.00")

        with pytest.raises(ValidationError, match="money ARRIVING"):
            _record(seed_user, line)

        assert _rows(seed_user) == []

    def test_a_line_ANOTHER_MATCH_already_claims_is_refused(
        self, app, db, seed_user,
    ):
        """Recorded twice is money counted twice."""
        line = _a_deposit(seed_user)
        _record(seed_user, line)
        db.session.flush()

        with pytest.raises(ValidationError, match="already matched"):
            _record(seed_user, line)

        assert len(_rows(seed_user)) == 1

    def test_a_line_on_ANOTHER_ACCOUNT_is_refused(
        self, app, db, seed_user, second_user,
    ):
        """The scope is the one statement of whose lines may be reached."""
        theirs = a_bank_line(
            second_user, an_import(second_user), amount="500.00",
        )

        with pytest.raises(ValidationError):
            scope = a_scope(seed_user)
            record_income_from_line(
                IncomeCreation(line_id=theirs.id), scope,
                RuleView.build(scope.owner_id, scope.account_id),
            )

        assert _rows(seed_user) == []

    def test_a_day_the_bank_puts_in_the_FUTURE_is_refused_before_any_write(
        self, app, db, seed_user,
    ):
        """R-EJ's refusal, asked BEFORE the row exists rather than after.

        ``mint_uncategorized`` writes and settles, and the settle verb is what
        refuses a day that has not happened yet -- so until 2026-08-27 this
        door wrote a settled row and then raised, leaving it for the batch's
        SAVEPOINT to take back.

        **Called DIRECTLY rather than through ``apply_reviewed``, and that is
        the whole point of this case**: the savepoint hides the defect, so a
        route-level test cannot see it.  Mutating the refusal away left the
        route case green; it fails here.  ``record_income_from_line`` is in
        ``__all__``, so a caller without a savepoint is a supported one.
        """
        ahead = display_today() + timedelta(days=3)
        line = _a_deposit(seed_user, posted_on=ahead)

        with pytest.raises(ValidationError, match="has not happened yet"):
            _record(seed_user, line)

        assert _rows(seed_user) == []

    def test_a_day_NO_SAVED_PERIOD_covers_is_refused_before_any_write(
        self, app, db, seed_user,
    ):
        """The refusal has to fire BEFORE the row exists, not after it.

        A settled row left behind by a refused act would be money the owner
        never accepted, and this door does not lean on the batch's SAVEPOINT
        to take it back -- which is the ordering ``mint_uncategorized``'s
        docstring states and this is what grades it.
        """
        beyond = seed_user["bootstrap_period"].end_date + timedelta(days=400)
        line = _a_deposit(seed_user, posted_on=beyond)

        with pytest.raises(ValidationError, match="No pay period covers"):
            _record(seed_user, line)

        assert _rows(seed_user) == []


class TestTheScreenOffersExactlyWhatTheDoorAccepts:
    """A control the door refuses is the shape this package keeps closing."""

    def test_an_OUTFLOW_is_not_offered(self, app, db, seed_user):
        """It has a create arm instead, which is a different card."""
        _a_deposit(seed_user, amount="-180.00")

        assert review_set(a_scope(seed_user)).recordable_inflows == ()

    def test_a_line_PAST_the_calendar_carries_no_period(
        self, app, db, seed_user,
    ):
        """The screen says so instead of rendering a doomed control.

        ``pay_period_id`` is ``None`` exactly where
        :meth:`ReviewScope.period_holding` would refuse, so the template's own
        condition and the door's refusal are one derivation.
        """
        beyond = seed_user["bootstrap_period"].end_date + timedelta(days=400)
        _a_deposit(seed_user, posted_on=beyond)

        offered = review_set(a_scope(seed_user)).recordable_inflows
        assert [item.pay_period_id for item in offered] == [None]


class TestThePassReportsIt:
    """A receipt that did not name this act would be one of two lies."""

    def test_a_pass_that_ONLY_records_a_deposit_did_not_move_nothing(
        self, app, db, seed_user,
    ):
        """``moved_nothing`` over a row this pass created and settled.

        The exact failure ``repriced_count`` was added for, one door over.
        """
        line = _a_deposit(seed_user)

        outcome = apply_reviewed(
            ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )

        assert outcome.deposited_count == 1
        assert outcome.moved_nothing is False

    def test_it_is_NOT_counted_as_a_purchase(self, app, db, seed_user):
        """``recorded_count``'s caption says *as a purchase*, and it is not."""
        line = _a_deposit(seed_user)

        outcome = apply_reviewed(
            ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )

        assert outcome.recorded_count == 0
        assert outcome.envelopes_created == 0

    def test_it_is_NOT_counted_as_a_row_MARKED_AS_HAVING_HAPPENED(
        self, app, db, seed_user,
    ):
        """The receipt may not claim the bank confirmed a record the owner had.

        This row goes through ``record_match`` in ``content.rows``, so it
        reaches ``_apply_day`` like any other member -- and it is ALREADY
        settled on the bank's own day by the time it gets there, so that door
        reports ``unchanged``.  Were it born Projected and settled by the
        match instead, the panel would read *"1 row(s) marked as having
        happened"* about a row this act is the only reason exists, which is
        exactly why a minted RESIDUAL is kept out of ``_apply_day``.
        """
        line = _a_deposit(seed_user)

        outcome = apply_reviewed(
            ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )

        assert (outcome.settled_count, outcome.corrected_count) == (0, 0)
        assert outcome.redated_count == 0

    def test_the_receipt_names_the_figure_and_says_it_has_no_category(
        self, app, db, seed_user,
    ):
        """The one thing the owner still has to act on."""
        line = _a_deposit(seed_user, amount="0.15")

        outcome = apply_reviewed(
            ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )

        summary = outcome.applied[0].summary
        assert "$0.15" in summary
        assert "no category" in summary

    def test_the_receipt_reports_it_in_the_BANKS_direction(
        self, app, db, seed_user,
    ):
        """POSITIVE, because the bank paid it in.

        The purchase arm negates its own figure onto this convention; doing
        the same here would report a deposit as a withdrawal.
        """
        line = _a_deposit(seed_user, amount="0.15")

        outcome = apply_reviewed(
            ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )

        assert outcome.applied[0].amount == Decimal("0.15")

    def test_a_REFUSED_deposit_does_not_cost_the_others(
        self, app, db, seed_user,
    ):
        """The ruled failure policy, over this door's own refusals."""
        good = _a_deposit(seed_user, amount="0.15")
        bad = _a_deposit(seed_user, amount="-180.00")

        outcome = apply_reviewed(
            ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(
                    IncomeCreation(line_id=bad.id),
                    IncomeCreation(line_id=good.id),
                ),
            ),
            a_scope(seed_user),
        )

        assert (outcome.deposited_count, outcome.refused_count) == (1, 1)
        assert "money ARRIVING" in outcome.refused[0].reason


class TestWhichConsentReachesThisDoor:
    """Which act classes a STANDING RULE may perform here (**R-GH**).

    **This class asserted the opposite until plan step
    ``bank_import:X-gj-2a``**, under the name ``TestNoRuleReachesThisDoor`` and
    ruling **bank_import:R-GW**'s reading that *a merchant answer says where
    SPENDING goes*, so no rule could mean *record this deposit*.  Ruling
    **R-HT(a)** amended the answer set with a member that says exactly what a
    deposit from a signature IS, so the boundary moved -- and the boundary that
    did NOT move is the one still asserted below.
    """

    def test_a_rule_consented_batch_MAY_carry_an_income(self, app, db, seed_user):
        """R-HT(a): filing a deposit is an act R-GH consents to once.

        It CREATES a row from a new bank line and modifies nothing the owner
        made by hand, which is the act class the ruling splits on -- so the
        batch is constructible where it used to raise.
        """
        batch = ReviewedBatch(
            consent=Consent.STANDING_RULE, matches=(), creations=(),
            incomes=(IncomeCreation(line_id=1),),
        )

        assert batch.item_count == 1

    def test_a_rule_consented_batch_carrying_a_MATCH_is_still_unconstructible(
        self, app, db, seed_user,
    ):
        """The boundary R-HT(a) did NOT move, and the reason it did not.

        **R-HT(b)'s group rule names a ROW SET**, which modifies rows the owner
        made by hand, so it applies only on their OK -- and that is exactly the
        act reaching ``accept_match``.  Kept unrepresentable rather than
        maintained, which is what the income arm's removal must not be read as
        weakening.
        """
        with pytest.raises(ValueError, match="cannot carry a match"):
            ReviewedBatch(
                consent=Consent.STANDING_RULE,
                # The submission's CONTENT is irrelevant here: the refusal is
                # about the act CLASS a rule may consent to, and it fires in
                # ``__post_init__`` before anything reads the item.
                matches=(
                    MatchSubmission(line_ids=frozenset({1}), rows=frozenset()),
                ),
                creations=(), incomes=(),
            )

    def test_a_TICKED_deposit_is_never_marked_applied_by_a_rule(
        self, app, db, seed_user,
    ):
        """R-GT's fact: the door records the consent it was GIVEN.

        The default is the one that claims less, so a caller that says nothing
        records a tick -- and this is the path an owner's own OK takes.
        """
        line = _a_deposit(seed_user)

        recorded = _record(seed_user, line)

        member = (
            db.session.query(StatementMatchMember)
            .filter(StatementMatchMember.match_id == recorded.match_id)
            .first()
        )
        assert member.match.applied_by_rule is False


class TestTheSafeguardAgainstRecordingWhatTheBooksHold:
    """Ruling **bank_import:R-GW**'s per-line duplicate check (``IncomeAlreadyRecorded``).

    **The only way this door can double-count money** is by recording a
    deposit the books already hold, and the signal the card was first written
    around -- the pass's own near-miss sentence -- fires only where some TIER
    admitted a candidate and declined it: 4 of 16 on the developer's own data,
    missing three payroll deposits worth `$7,838.92` whose app rows sit outside
    every tier's bound.  This class grades the fact that replaced it.  It had
    no test at all until an adversarial review 2026-08-27 said so.
    """

    def test_it_NAMES_the_rows_the_period_already_holds(
        self, app, db, seed_user,
    ):
        """The count, the total and the labels, so the owner can find them."""
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Phone Allowance", amount="39.54", income=True,
        )
        # NOT the sum of the two rows, and not either of them: an exact
        # figure is the proposer's to claim, and this case is about the method.
        line = _a_deposit(seed_user, amount="2600.00")

        held = review_set(a_scope(seed_user)).income_already_recorded_in(line)

        assert held is not None
        assert {row.label for row in held.rows} == {
            salary.name, allowance.name,
        }
        assert held.total == Decimal("2512.92")

    def test_a_period_holding_NOTHING_unexplained_says_nothing(
        self, app, db, seed_user,
    ):
        """The state that makes recording safe gets no sentence at all."""
        line = _a_deposit(seed_user, amount="2600.00")

        assert review_set(
            a_scope(seed_user),
        ).income_already_recorded_in(line) is None

    def test_rows_in_ANOTHER_period_do_not_warn(self, app, db, seed_user):
        """The question is about THIS deposit's paycheck, not the account.

        Without the span test the sentence would fire on every deposit an
        account with any unexplained income holds, which is the warning-on-
        every-row shape this arc measures money going through.

        **A SECOND line is recorded in the later period, and it is what makes
        this case fire.**  ``unmatched_rows`` only offers a row whose period
        overlaps the span the account's statements COVER
        (``_could_have_been_shown``), so with one line in the bootstrap period
        the later-period row is absent from the set entirely -- and the case
        passed while measuring that absence rather than the period test.
        Mutating the period clause to ``and True`` left it green.  Found by
        mutation testing this step's own controls 2026-08-27; the assertion
        that the row IS offered is what keeps it honest.
        """
        later = a_later_period(seed_user)
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
            period=later,
        )
        _a_deposit(
            seed_user, amount="17.00", posted_on=later.start_date,
            sequence_in_group=1,
        )
        line = _a_deposit(
            seed_user, amount="2600.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )

        review = review_set(a_scope(seed_user))

        # The row really is on offer -- otherwise this measures its absence.
        assert salary.name in {row.label for row in review.unmatched_rows}
        assert review.income_already_recorded_in(line) is None

    def test_a_deposit_SMALLER_than_the_smallest_row_says_nothing(
        self, app, db, seed_user,
    ):
        """The proof that keeps this a signal rather than noise.

        Every unexplained row is positive, so no subset of them can come to
        less than the smallest -- a `$0.15` dividend cannot BE a `$2,473.38`
        salary row at any tolerance.  These eight lines are what ruling
        **bank_import:R-GW** exists for, and a sentence on them would be the alarm that
        teaches an owner to stop reading alarms.
        """
        a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        line = _a_deposit(seed_user, amount="0.15")

        assert review_set(
            a_scope(seed_user),
        ).income_already_recorded_in(line) is None

    def test_a_deposit_AT_OR_ABOVE_the_smallest_row_DOES_warn(
        self, app, db, seed_user,
    ):
        """The boundary the proof turns on, asserted from the other side.

        A test for the suppression alone would pass if the method returned
        ``None`` for everything, so the two are written together.

        **The figure matches no row and no sum of them**, deliberately: an
        exact-amount pair is claimed by the proposer, which takes the row out
        of ``unmatched_rows`` and would make this case measure the proposer
        rather than this method.
        """
        a_transaction(
            seed_user, name="Phone Allowance", amount="39.54", income=True,
        )
        a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        line = _a_deposit(seed_user, amount="60.00")

        held = review_set(a_scope(seed_user)).income_already_recorded_in(line)

        assert held is not None
        assert held.total == Decimal("2512.92")

    def test_an_EXPENSE_row_is_not_income_the_books_hold(
        self, app, db, seed_user,
    ):
        """A deposit is never explained by a bill the bank has not shown."""
        a_transaction(seed_user, name="Electricity", amount="180.00")
        line = _a_deposit(seed_user, amount="2600.00")

        assert review_set(
            a_scope(seed_user),
        ).income_already_recorded_in(line) is None


class TestWhichLinesGetAnActAndWhichSTILLDoNot:
    """The gap this step closed, and the one it does NOT -- both measured.

    ``creatable`` takes ``amount < 0``, ``recordable_inflows`` takes
    ``amount > 0``, and ``ck_bank_statement_lines_amount_real_nonzero``
    declares ``amount <> 0`` -- so by SIGN the two are total.  They are not
    total as LISTS, and a first draft of this class asserted that they were:
    ``_creatable_lines`` drops an outflow the bank dates MADE after it POSTED
    before the split (finding **N-325**), and that line reaches none of the
    three lists while still being counted by ``awaiting_review_count``.  Named
    by this step's own adversarial review 2026-08-27.
    """

    @pytest.mark.parametrize("amount", ["-180.00", "0.15"])
    def test_every_ORDINARY_unexplained_line_is_offered_SOME_act(
        self, app, db, seed_user, amount,
    ):
        """One card or the other, and never neither.

        Before this step ``creatable`` took the outflows and NOTHING took the
        other side, so an inflow appeared in ``unmatched`` -- and therefore in
        the awaiting count -- with no control anywhere that could dispose of
        it.  This is that hole as a test: whichever direction the line runs,
        exactly one of the two lists holds it.

        **No app row is staged**, deliberately: a `$180.00` envelope beside a
        `-$180.00` line is an exact match, so the proposer would claim the line
        and this case would pass while measuring the proposer instead.
        """
        line = _a_deposit(seed_user, amount=amount)

        review = review_set(a_scope(seed_user))

        offered = (
            [item.line.line_id for item in review.creatable]
            + [item.line.line_id for item in review.recordable_inflows]
        )
        assert offered == [line.id]

    def test_an_IMPOSSIBLE_DAY_outflow_still_reaches_NO_list(
        self, app, db, seed_user,
    ):
        """The class bank_import:R-GW did NOT close, pinned so it cannot be forgotten.

        An outflow the bank dates as MADE after it POSTED has no day a
        purchase could happen on, so ``_creatable_lines`` declines it and
        counts it on ``ReviewBounds`` instead -- finding **N-325**, ruled
        *reported rather than repaired* 2026-08-19 because the alternative
        decides which day the app believes when the bank contradicts itself.

        It is therefore in ``unmatched`` -- and in the grid's awaiting count --
        with no control anywhere, which is the SAME shape this step closed for
        inflows.  This case exists so that claim stays honest: if a later step
        gives the class an act, this fails and says so.
        """
        day = seed_user["bootstrap_period"].start_date
        line = _a_deposit(
            seed_user, amount="-42.00",
            posted_on=day, transaction_on=day + timedelta(days=1),
        )

        review = review_set(a_scope(seed_user))

        assert review.creatable == ()
        assert review.parked == ()
        assert review.recordable_inflows == ()
        assert [item.line_id for item in review.unmatched] == [line.id]
        assert review.bounds.impossible_day_count == 1

    def test_a_ZERO_line_cannot_EXIST(self, app, db, seed_user):
        """The constraint the two doors' totality rests on, graded directly.

        A FIRING control on a database guarantee rather than on app code: drop
        ``amount <> 0`` and this fails, which is the warning that the
        ``<= 0`` / ``>= 0`` comparisons in the two doors have become reachable
        arms that no case covers.
        """
        with pytest.raises(IntegrityError, match="amount_real_nonzero"):
            _a_deposit(seed_user, amount="0.00")

        db.session.rollback()


class TestTheCARDsOKFilesUnderTheSameAnswerTheCARDSTATES:
    """The automatic door and the press are ONE derivation (**R-HT(a)**).

    **This class exists because they were two** (adversarial code review
    2026-08-31).  The category rode on :class:`IncomeCreation`, set only by
    :meth:`~._placement.InflowPlacement.creation_for`, which the import-time
    rule pass reaches and no route does -- so the Reconcile card rendered *Add
    as Interest income*, the owner pressed OK, and an UNCATEGORIZED row was
    written.  The figure, the day and the period were right; what was wrong is
    that the card asserted something the door did not do.

    **It is reachable on the ordinary sequence**, not a corner: import, then
    state the rule on the merchants page, then return to Reconcile.  The line
    was never `fresh` by then, so the card is what the owner acts on.
    """

    def test_a_TICKED_deposit_is_filed_under_the_rule_the_card_names(
        self, app, db, seed_user,
    ):
        """The whole point: what the card says and what the door writes agree.

        Driven through ``apply_reviewed`` with ``Consent.TICKED`` -- the door
        the page's Apply reaches -- rather than through the rule pass, because
        the rule pass was never the broken half.
        """
        category = seed_user["categories"]["Salary"]
        a_rule(
            seed_user, "Dividend Earned", income_category_id=category.id,
        )
        line = _a_deposit(seed_user, merchant="Dividend Earned")
        db.session.commit()

        outcome = apply_reviewed(
            ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )
        db.session.flush()

        assert outcome.deposited_count == 1, outcome.refused
        recorded = (
            db.session.query(Transaction)
            .filter(Transaction.account_id == seed_user["account"].id)
            .order_by(Transaction.id.desc())
            .first()
        )
        assert recorded.category_id == category.id

    def test_a_TICKED_deposit_with_NO_rule_is_still_uncategorized(
        self, app, db, seed_user,
    ):
        """The pairing, so the case above cannot pass by always categorising.

        A deposit no rule answers is what this door has always written and
        must keep writing: the app does not know what it is, and inventing an
        answer is the misfiling ruling **R-FN** refused.
        """
        line = _a_deposit(seed_user, merchant="Someone New")
        db.session.commit()

        outcome = apply_reviewed(
            ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(),
                incomes=(IncomeCreation(line_id=line.id),),
            ),
            a_scope(seed_user),
        )
        db.session.flush()

        assert outcome.deposited_count == 1, outcome.refused
        recorded = (
            db.session.query(Transaction)
            .filter(Transaction.account_id == seed_user["account"].id)
            .order_by(Transaction.id.desc())
            .first()
        )
        assert recorded.category_id is None
