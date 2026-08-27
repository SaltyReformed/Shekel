"""A GROUP's difference: the member the door mints, and what it refuses.

Plan step **bank_import:X-f6d-4**, rulings **R-GD(a)** and **R-FN**.

**The step's own acceptance test is finding N-239's payroll shape**, and it is
the shape most of these cases stage: a bank deposit of `$2,573.43` against two
app rows summing `$2,573.38`.  On a production clone carrying the developer's
own 376 recorded lines that happens SEVEN times, `$0.04`-`$0.06` each and
`+$0.35` in total, and before this step every one of them was refused outright
-- so the deposit could not be recorded at all and its member rows never had
their day corrected either.

**What the step promises, and what each class below pins:**

* the difference becomes an ordinary row with NO CATEGORY, settled on the
  bank's day, which books to the per-owner Uncategorized ledger account;
* the row is a MEMBER of the match, so ``Sigma(lines) == Sigma(members)`` holds
  after the act -- the balance test stops being a fence and becomes an
  invariant;
* the FIGURE the owner accepted is reconciled against the door's own, and a
  disagreement refuses;
* one ROW is determinate however many LINES explain it, so it takes the bank's
  figure and mints nothing;
* the refusals that are about a row, and the one that is about the pair, still
  fire.

Every refusal here is a FIRING CONTROL: written to fail if the refusal were
deleted, which is the standard ``docs/plans/verification.md`` sets.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    SettledDayBasisEnum,
    SettlementBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.exceptions import ValidationError
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.statement_match import StatementMatchMember
from app.models.transaction import Transaction
from app.services import statement_match
from app.services.statement_match._variance import MatchSides

from ._builders import (
    a_bank_line,
    a_later_period,
    a_purchase,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)


def _submit(seed_user, lines=(), transactions=(), entries=(), residual=None):
    """Accept a match naming exactly these subjects.

    Derived per call, so every submission sees the rows the test has staged,
    and the SAME scope builds the submission and applies it -- the two-moment
    flow the screen has (finding **N-336**).

    Args:
        seed_user: The seeded user bundle.
        lines: Bank line rows.
        transactions: Transaction rows.
        entries: Purchase rows.
        residual: The difference the screen showed and the owner ticked.

    Returns:
        The :class:`~app.services.statement_match.AcceptedMatch`.
    """
    scope = a_scope(seed_user)
    return statement_match.accept_match(
        a_submission(
            scope, lines=lines, transactions=transactions, entries=entries,
            residual=residual,
        ),
        scope,
    )


def _minted(seed_user):
    """Return every uncategorized row on the seeded owner's checking account.

    **Keyed on ``category_id IS NULL`` rather than on the name**, because the
    name is a display decision this step also makes and a test that asserted
    both through one predicate would pass a rename it should have caught.

    **A transfer SHADOW is excluded by the column that says it is one.**  The
    ``create_transfer`` fixture leaves a shadow's category NULL, so without
    that term this helper reports one as a row this door minted -- which it
    measurably did, in a first version of the group cases below.  It is a
    property of the FIXTURE and not of the app: all 346 shadows on a
    production clone carry a category (measured 2026-08-23, against 0
    NULL-category transactions of any kind).

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The rows, ascending by id.
    """
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == seed_user["account"].id,
            Transaction.category_id.is_(None),
            Transaction.transfer_id.is_(None),
        )
        .order_by(Transaction.id)
        .all()
    )


def _uncategorized_net(seed_user, ledger_class):
    """Return the debit-positive net of the per-owner Uncategorized account.

    **The proof is over the LEDGER and not over the row**, because the row
    carrying no category is only half the claim: what makes ruling **R-FN**'s
    mechanism real is that ``posting_service`` routes such a row's counter leg
    to the fallback account.  Measured 2026-08-23 on a production clone, no
    fallback account existed and no transaction carried a NULL category, so
    this door is the first writer of both and nothing else in the app would
    notice if the routing were wrong.

    Args:
        seed_user: The seeded user bundle.
        ledger_class: Income or Expense
            (:class:`~app.enums.LedgerAccountClassEnum`).

    Returns:
        The account's net, or ``None`` when no such account exists yet.
    """
    account = (
        db.session.query(LedgerAccount)
        .filter(
            LedgerAccount.user_id == seed_user["user"].id,
            LedgerAccount.is_fallback.is_(True),
            LedgerAccount.class_id == ref_cache.ledger_account_class_id(
                ledger_class,
            ),
        )
        .one_or_none()
    )
    if account is None:
        return None
    return db.session.query(
        db.func.coalesce(db.func.sum(Posting.amount), Decimal("0.00")),
    ).filter(Posting.ledger_account_id == account.id).scalar()


@dataclass(frozen=True)
class _ALine:
    """A bank line, as :meth:`MatchSides.of` structurally types one.

    A value rather than an ORM row, because the point of the cases using it is
    a figure ``bank_statement_lines.amount`` is too narrow to hold.
    """

    amount: Decimal


@dataclass(frozen=True)
class _ARow:
    """An app row, as :meth:`MatchSides.of` structurally types one."""

    cash_amount: Decimal


def _a_transfer_shadow(seed_user, *, amount="100.00"):
    """Return the EXPENSE leg of a transfer off the seeded checking account.

    Built through ``transfer_service.create_transfer``, the sole creation
    chokepoint, so the pair and its invariants are the real ones -- a
    hand-staged shadow would grade a row the app cannot produce.

    Args:
        seed_user: The seeded user bundle.
        amount: The transfer amount.

    Returns:
        The shadow :class:`~app.models.transaction.Transaction` on checking.
    """
    # pylint: disable=import-outside-toplevel -- one caller, and the transfer
    # service is not otherwise part of this module's subject.
    from app.services import account_service
    from tests._test_helpers import create_transfer

    destination = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name="Savings",
            anchor_balance=Decimal("100.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        )
    )
    db.session.flush()
    transfer = create_transfer(
        seed_user, db.session, seed_user["account"], destination,
        seed_user["bootstrap_period"], amount=Decimal(amount),
    )
    db.session.flush()
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.transfer_id == transfer.id,
            Transaction.account_id == seed_user["account"].id,
        )
        .one()
    )


def _payroll(seed_user, bank="2573.43"):
    """Stage finding N-239's shape: a deposit the app's rows fall short of.

    Args:
        seed_user: The seeded user bundle.
        bank: What the bank actually paid.

    Returns:
        ``(line, salary, allowance)``.
    """
    statement = an_import(seed_user)
    line = a_bank_line(
        seed_user, statement, amount=bank,
        posted_on=seed_user["bootstrap_period"].start_date,
    )
    salary = a_transaction(
        seed_user, name="Salary", amount="2473.38", income=True,
    )
    allowance = a_transaction(
        seed_user, name="Allowance", amount="100.00", income=True,
    )
    return line, salary, allowance


class TestTheDifferenceBecomesARowTheOwnerAccepts:
    """Ruling **R-FN**, on the payroll shape that motivated the step."""

    def test_the_group_is_recorded_and_a_row_is_minted(
        self, app, db, seed_user,
    ):
        """The whole act: the deposit records and the five cents exists."""
        line, salary, allowance = _payroll(seed_user)

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert accepted.residual == Decimal("0.05")
        assert salary.settled_on == line.posted_on
        assert allowance.settled_on == line.posted_on
        rows = _minted(seed_user)
        assert len(rows) == 1
        assert rows[0].estimated_amount == Decimal("0.05")

    def test_the_minted_row_is_income_settled_on_the_banks_day(
        self, app, db, seed_user,
    ):
        """Every column the row is born with, asserted rather than assumed."""
        line, salary, allowance = _payroll(seed_user)

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        row = _minted(seed_user)[0]
        assert row.category_id is None
        assert row.transaction_type_id == ref_cache.txn_type_id(
            TxnTypeEnum.INCOME,
        )
        assert row.status_id == ref_cache.status_id(StatusEnum.RECEIVED)
        assert row.settled_on == line.posted_on
        assert row.settled_day_basis_id == ref_cache.settled_day_basis_id(
            SettledDayBasisEnum.OBSERVED,
        )
        assert row.settled_amount == Decimal("0.05")
        assert row.is_envelope is False
        # It OWNS its figure: no template, no transfer, no card spend, so
        # ``ck_transactions_amount_ownership`` requires the stored pair.
        assert row.amount_source_id is None
        assert row.template_id is None
        assert row.account_id == seed_user["account"].id

    def test_a_shortfall_the_bank_TOOK_is_an_expense(
        self, app, db, seed_user,
    ):
        """The other direction, which no payroll case can reach.

        The sign of the difference decides the transaction TYPE, and a test
        over income alone would pass an implementation that hardcoded it.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-180.06",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        one = a_transaction(seed_user, name="Power", amount="100.00")
        two = a_transaction(seed_user, name="Water", amount="80.00")

        accepted = _submit(
            seed_user, lines=[line], transactions=[one, two],
            residual="-0.06",
        )

        assert accepted.residual == Decimal("-0.06")
        row = _minted(seed_user)[0]
        assert row.transaction_type_id == ref_cache.txn_type_id(
            TxnTypeEnum.EXPENSE,
        )
        assert row.estimated_amount == Decimal("0.06")
        assert row.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_it_is_named_for_the_banks_own_merchant(
        self, app, db, seed_user,
    ):
        """Developer's decision 2026-08-23: what it is, then who paid it."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.43",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL 0261130059978",
            merchant="TOWN OF CLAYTON PAYROLL",
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert _minted(seed_user)[0].name == (
            "Statement difference: TOWN OF CLAYTON PAYROLL"
        )

    def test_a_MULTI_LINE_group_is_named_for_the_line_it_posts_on(
        self, app, db, seed_user,
    ):
        """One rule for the day and the name, so they describe one movement.

        A match's day is ``max(posted_on)`` over its lines, and the row is
        named for that same line -- so a row dated 06-10 is never captioned
        with a merchant that paid on 06-08.  A mutation sweep found this
        ungraded: every other case here stages ONE line, and with one line
        ``min`` and ``max`` are the same answer.
        """
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        earlier = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
            description="POINT OF SALE DEBIT LOWES", merchant="Lowe's",
        )
        later = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=first + timedelta(days=2),
            description="POINT OF SALE DEBIT WAL-MART", merchant="Walmart",
        )
        one = a_transaction(seed_user, name="Power", amount="100.00")
        two = a_transaction(seed_user, name="Water", amount="80.00")

        accepted = _submit(
            seed_user, lines=[earlier, later], transactions=[one, two],
            residual="-0.06",
        )

        row = _minted(seed_user)[0]
        assert accepted.posts_on == later.posted_on
        assert row.settled_on == later.posted_on
        assert row.name == "Statement difference: Walmart"

    def test_it_lands_in_the_period_holding_the_banks_day(
        self, app, db, seed_user,
    ):
        """The money moved on the bank's day, so that paycheck holds it.

        Staged so the members sit in a DIFFERENT period from the line, which
        is what separates "the period the bank's day is in" from "the period
        the other members are in" -- an implementation reading the first
        member's period would pass without it.
        """
        later = a_later_period(seed_user)
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=later.start_date,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
            settled_on=later.start_date, status=StatusEnum.RECEIVED,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
            settled_on=later.start_date, status=StatusEnum.RECEIVED,
        )

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        row = _minted(seed_user)[0]
        assert row.pay_period_id == later.id
        assert salary.pay_period_id == seed_user["bootstrap_period"].id


class TestTheTwoSidesAreRoundedBeforeTheyAreCompared:
    """``MatchSides``' own rule, graded on a value the database cannot hold.

    Every figure the app can currently produce is a whole number of cents, so
    on real data this rounding is a no-op and the three ways of writing it --
    round each side, round the difference, round neither -- all agree.  What
    picks this one is that BOTH derived values have to be storable: the
    difference becomes an ``estimated_amount`` on a ``Numeric(12, 2)`` column,
    and ``bank_cash_for`` returns the bank side verbatim as the figure a
    one-row match WRITES.  A sweep found the choice ungraded, which is how a
    decision becomes an accident.

    It is graded through the constructor with plain values rather than through
    the door, because that is the only way to hand it a sub-cent figure at all.
    """

    def test_each_side_is_rounded_HALF_UP_away_from_zero(self):
        """Half a cent up on the magnitude, which is ``round_money``'s rule."""
        sides = MatchSides.of(
            [_ALine(Decimal("100.005"))],
            [_ARow(Decimal("-100.005"))],
        )

        assert sides.bank == Decimal("100.01")
        assert sides.app == Decimal("-100.01")
        assert sides.difference == Decimal("200.02")

    def test_the_difference_is_always_a_whole_number_of_cents(self):
        """Which is what makes it storable, and what the browser mirrors."""
        sides = MatchSides.of(
            [_ALine(Decimal("2573.43"))],
            [_ARow(Decimal("2473.384")), _ARow(Decimal("100.00"))],
        )

        assert sides.app == Decimal("2573.38")
        assert sides.difference == Decimal("0.05")
        assert sides.difference.as_tuple().exponent == -2


class TestTheLedgerBooksItToUncategorized:
    """Ruling **R-FN**'s mechanism, graded where it actually happens."""

    def test_an_income_difference_reaches_Uncategorized_Income(
        self, app, db, seed_user,
    ):
        """The fallback account is created and holds the five cents.

        Income is credit-normal, so a `+$0.05` receipt is `-0.05` on the
        debit-positive ledger.
        """
        line, salary, allowance = _payroll(seed_user)
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) is None

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) == Decimal("-0.05")

    def test_an_expense_difference_reaches_Uncategorized_Expense(
        self, app, db, seed_user,
    ):
        """The other class, which is a SECOND row keyed by class."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-180.06",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        one = a_transaction(seed_user, name="Power", amount="100.00")
        two = a_transaction(seed_user, name="Water", amount="80.00")

        _submit(
            seed_user, lines=[line], transactions=[one, two],
            residual="-0.06",
        )

        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.EXPENSE,
        ) == Decimal("0.06")

    def test_the_entry_it_posts_BALANCES(self, app, db, seed_user):
        """Both legs, so the row moves cash as well as filling a bucket.

        The counter leg alone would satisfy the two cases above while booking
        nothing against the account the bank statement is about.
        """
        line, salary, allowance = _payroll(seed_user)

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        row = _minted(seed_user)[0]
        legs = (
            db.session.query(
                LedgerAccount.is_fallback, db.func.sum(Posting.amount),
            )
            .join(Posting, Posting.ledger_account_id == LedgerAccount.id)
            .join(JournalEntry, JournalEntry.id == Posting.journal_entry_id)
            .filter(JournalEntry.transaction_id == row.id)
            .group_by(LedgerAccount.is_fallback)
            .all()
        )
        assert dict(legs) == {True: Decimal("-0.05"), False: Decimal("0.05")}


class TestTheMintedRowIsAMemberOfTheMatch:
    """The identity holds by CONSTRUCTION, which is the step's whole shape."""

    def test_it_is_recorded_as_a_member(self, app, db, seed_user):
        """Three members for two submitted rows."""
        line, salary, allowance = _payroll(seed_user)

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        row = _minted(seed_user)[0]
        member = (
            db.session.query(StatementMatchMember)
            .filter(StatementMatchMember.transaction_id == row.id)
            .one()
        )
        assert member.match_id == accepted.match_id

    def test_the_accepted_group_AGREES(self, app, db, seed_user):
        """The panel's own re-derivation of the balance this door checked.

        Without the minted row as a member the group would read out of
        agreement forever, on the very act that made it add up.
        """
        line, salary, allowance = _payroll(seed_user)

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        groups = statement_match.review_set(
            a_scope(seed_user),
        ).accepted
        assert len(groups) == 1
        assert groups[0].agrees is True
        assert sum(row.cash_amount for row in groups[0].rows) == line.amount

    def test_the_next_pass_does_NOT_re_offer_it(self, app, db, seed_user):
        """A member is claimed, so nothing may match it to something else."""
        line, salary, allowance = _payroll(seed_user)

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        row = _minted(seed_user)[0]
        review = statement_match.review_set(a_scope(seed_user))
        assert not [r for r in review.unmatched_rows if r.row_id == row.id]

    def test_releasing_the_match_REMOVES_the_row_it_created(
        self, app, db, seed_user,
    ):
        """Developer ruling 2026-08-23, and the postings go with it.

        A settle day survives an undo because it is a fact about money that
        moved; a group's DIFFERENCE is a fact about the grouping, and once the
        grouping is released it states nothing.  So the row goes and its ledger
        legs are reversed -- the Uncategorized bucket is back where it started.
        """
        line, salary, allowance = _payroll(seed_user)
        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )
        row_id = _minted(seed_user)[0].id
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) == Decimal("-0.05")

        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert db.session.get(Transaction, row_id) is None
        assert not _minted(seed_user)
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) == Decimal("0.00")

    def test_UNDO_then_RE_ACCEPT_does_not_double_book(
        self, app, db, seed_user,
    ):
        """The defect the ruling exists to close, as its own control.

        Reproduced by adversarial security review 2026-08-23 in two ordinary
        clicks: undo kept the row, the bank line went back to unexplained, and
        accepting the same group again left TWO `$0.05` rows for one `$0.05`
        difference -- the balance high for good with nothing naming it.
        """
        line, salary, allowance = _payroll(seed_user)
        first = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )
        statement_match.release_match(
            first.match_id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert len(_minted(seed_user)) == 1
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) == Decimal("-0.05")

    def test_a_created_row_the_owner_has_EDITED_refuses_the_undo(
        self, app, db, seed_user,
    ):
        """Their record now, so this act may not take it.

        Deleting it would throw away their work in order to tidy a relation,
        which is the direction the release already refuses to go for a settle
        day.  The predicate is the row's own revision counter, so ANY edit
        counts rather than a guessed-at list of columns.
        """
        line, salary, allowance = _payroll(seed_user)
        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )
        row = _minted(seed_user)[0]
        row.category_id = seed_user["categories"]["Groceries"].id
        db.session.flush()

        with pytest.raises(ValidationError) as caught:
            statement_match.release_match(
                accepted.match_id, seed_user["user"].id,
                seed_user["account"].id,
            )

        assert "you have edited that row since" in str(caught.value)
        # And the refusal left the act standing, so nothing is half-undone.
        assert db.session.get(Transaction, row.id) is not None
        assert statement_match.review_set(a_scope(seed_user)).accepted

    def test_a_member_the_act_did_NOT_create_survives_the_undo(
        self, app, db, seed_user,
    ):
        """The rule is what this act CREATED, never what it named.

        Without that distinction an undo would delete the owner's own rows,
        which is the opposite of what a release is for.
        """
        line, salary, allowance = _payroll(seed_user)
        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert db.session.get(Transaction, salary.id) is not None
        assert db.session.get(Transaction, allowance.id) is not None
        # ...and the question is restored: both are matchable again.
        review = statement_match.review_set(a_scope(seed_user))
        offered = {row.row_id for row in review.unmatched_rows}
        assert {salary.id, allowance.id} <= offered


class TestTheFigureTheOwnerAcceptedIsReconciled:
    """Finding **N-336**'s lesson on the one number no per-row guard sees."""

    def test_a_difference_the_screen_got_WRONG_is_refused(
        self, app, db, seed_user,
    ):
        """The control that makes carrying the figure worth anything.

        A browser that sums wrong states a correction the per-row staleness
        guard cannot see -- every row agrees with what was reviewed, and the
        SUM is the thing that moved.
        """
        line, salary, allowance = _payroll(seed_user)

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
                residual="-1006.00",
            )

        assert "-1,006.00" in str(caught.value)
        assert "+0.05" in str(caught.value)
        assert not _minted(seed_user)
        assert salary.settled_on is None

    def test_a_SUB_CENT_figure_is_not_the_doors_own_and_is_refused(
        self, app, db, seed_user,
    ):
        """The half-cent hole a quantizer opened, closed at the door.

        The schema used to round ``0.054`` to ``0.05`` and hand the door a
        figure that agreed -- so the consent gate the whole design rests on had
        a tolerance, asymmetric and in the rounding mode
        ``app.utils.money`` forbids.  The reader passes it through now and this
        is what turns it away.  Found by adversarial design review 2026-08-23.
        """
        line, salary, allowance = _payroll(seed_user)

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
                residual="0.054",
            )

        assert "reviewed against a difference of +0.05" in str(caught.value)
        assert not _minted(seed_user)

    def test_a_difference_accepted_where_there_is_NONE_is_refused(
        self, app, db, seed_user,
    ):
        """The sides agree, so a screen offering to record one described an
        act this door will not perform.

        **Its own arm, because equality does NOT catch it**: the door's
        difference is ``0.00``, and a submitted ``0.00`` is equal to it.  Two
        independent reviews measured a first version's docstring claiming
        otherwise on the same day.
        """
        line, salary, allowance = _payroll(seed_user, bank="2573.38")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
                residual="0.05",
            )

        assert "no difference of +0.05 to record" in str(caught.value)
        assert not _minted(seed_user)
        assert salary.settled_on is None

    def test_a_ZERO_difference_accepted_on_an_agreeing_group_is_refused(
        self, app, db, seed_user,
    ):
        """The case the equality arm reads as agreement.

        Nothing would be minted either way (the mint is gated on a non-zero
        difference), so the mutation that deletes this arm is invisible to
        every test that submits a non-zero figure -- which is how a sweep found
        it surviving.
        """
        line, salary, allowance = _payroll(seed_user, bank="2573.38")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
                residual="0.00",
            )

        assert "no difference of +0.00 to record" in str(caught.value)
        assert salary.settled_on is None

    def test_ONE_row_takes_the_banks_figure_and_mints_NOTHING(
        self, app, db, seed_user,
    ):
        """The two remedies are exclusive, and this is the other one.

        One row apportions nothing, so the difference is written to it.  A
        first version of this step gated the mint on the owner's consent
        alone, so a one-row match carrying one did BOTH -- the row corrected
        AND the same difference booked again to Uncategorized.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-178.29",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        txn = a_transaction(seed_user, amount="178.32")

        # The bank took LESS than the row says, so the difference is POSITIVE
        # on the cash convention both sides use: -178.29 - (-178.32) = +0.03.
        accepted = _submit(
            seed_user, lines=[line], transactions=[txn], residual="0.03",
        )

        assert accepted.repriced_count == 1
        assert accepted.residual is None
        assert txn.settled_amount == Decimal("178.29")
        assert not _minted(seed_user)

    def test_the_SAME_group_without_a_residual_still_refuses(
        self, app, db, seed_user,
    ):
        """The firing control for the whole arm: delete the consent and the
        act is refused exactly as it was before this step."""
        line, salary, allowance = _payroll(seed_user)

        with pytest.raises(ValidationError) as caught:
            _submit(seed_user, lines=[line], transactions=[salary, allowance])

        assert "+0.05" in str(caught.value)
        assert not _minted(seed_user)


class TestOneRowIsDeterminateHoweverManyLinesExplainIt:
    """Developer's decision 2026-08-23, widening ``bank_cash_for``.

    Two lines against one row apportion nothing either: their SUM is what the
    bank says that row is worth.  Before this step such a match was refused as
    a group, and once a group's difference became recordable it would have
    minted a residual beside a row the bank had just priced exactly.
    """

    def test_two_lines_CORRECT_the_one_row_they_name(
        self, app, db, seed_user,
    ):
        """The row takes the lines' total; nothing is minted."""
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        line_a = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
        )
        line_b = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=first + timedelta(days=1),
        )
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(
            seed_user, lines=[line_a, line_b], transactions=[txn],
            residual="-0.06",
        )

        assert accepted.repriced_count == 1
        assert accepted.residual is None
        assert txn.settled_amount == Decimal("180.06")
        assert not _minted(seed_user)

    def test_two_lines_with_NO_accepted_figure_are_refused(
        self, app, db, seed_user,
    ):
        """The widening needed a consent gate and a first version had none.

        Nothing bounds which lines an owner may tick, so without one a mis-tick
        rewrites a row's amount to an arbitrary sum in a single press with no
        undo.  Only the near tier's single-line one-row shape is exempt.
        """
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        line_a = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
        )
        line_b = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=first + timedelta(days=1),
        )
        txn = a_transaction(seed_user, amount="180.00")

        with pytest.raises(ValidationError) as caught:
            _submit(seed_user, lines=[line_a, line_b], transactions=[txn])

        assert "-0.06" in str(caught.value)
        assert txn.settled_amount is None

    def test_ONE_line_against_ONE_row_needs_no_accepted_figure(
        self, app, db, seed_user,
    ):
        """The near tier's own shape: its tick carries no difference field.

        Requiring one here would kill the tier `X-f6d-1` shipped, so the
        exemption is real -- and it is the ONLY one.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-178.29",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        txn = a_transaction(seed_user, amount="178.32")

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert accepted.repriced_count == 1
        assert txn.settled_amount == Decimal("178.29")

    def test_a_TRANSFER_SHADOW_in_a_group_refuses_the_difference(
        self, app, db, seed_user,
    ):
        """Transfer invariant 3, carried into the group arm.

        A shadow's amount is held equal to its parent's, so correcting one
        means correcting the TRANSFER -- and recording the gap beside it as an
        uncategorized row would book money the transfer already accounts for.
        A first version of this step asked this only of a LONE row, so ticking
        one extra turned the refusal into a residual.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.06", posted_on=bank_day,
        )
        shadow = _a_transfer_shadow(seed_user, amount="100.00")
        other = a_transaction(seed_user, name="Power", amount="80.00")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[shadow, other],
                residual="-0.06",
            )

        assert "one half of a transfer" in str(caught.value)
        assert not _minted(seed_user)

    def test_two_lines_against_a_row_that_CANNOT_be_corrected_still_refuse(
        self, app, db, seed_user,
    ):
        """The widening moved the SHAPE test, not the row refusals."""
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        line_a = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
        )
        line_b = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=first + timedelta(days=1),
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="180.00", is_envelope=True,
        )
        a_purchase(seed_user, envelope, amount="180.00")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line_a, line_b], transactions=[envelope],
            )

        assert "no figure of its own" in str(caught.value)
        assert not _minted(seed_user)


class TestThePairMustBeTheSameDirection:
    """The sign check, which a GROUP could not reach until this step.

    It was short-circuited by the group's own refusal, and that stopped being
    harmless the moment a group's difference became something this door
    WRITES: a `-100.00` line against a `+50.00` row would otherwise have
    recorded `-150.00` of spending nobody claimed.
    """

    def test_a_GROUP_whose_sides_oppose_is_refused(self, app, db, seed_user):
        """Two income rows against a debit line."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-100.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        one = a_transaction(
            seed_user, name="Refund", amount="30.00", income=True,
        )
        two = a_transaction(
            seed_user, name="Rebate", amount="20.00", income=True,
        )

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[one, two],
                residual="-150.00",
            )

        assert "not the same movement" in str(caught.value)
        assert not _minted(seed_user)

    def test_a_ZERO_side_opposes_NOTHING(self, app, db, seed_user):
        """A side with no money in it has no direction.

        A bare ``(bank < 0) != (app < 0)`` reads zero as "money in", so rows
        netting to nothing opposed a DEBIT line and not a CREDIT one -- the
        same submission refused or accepted according to the bank's sign.
        Found by adversarial security review 2026-08-23; graded on the arm
        that was wrongly refusing, because the other one already passed.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-500.00", posted_on=bank_day,
        )
        out = a_transaction(seed_user, name="Power", amount="30.00")
        back = a_transaction(
            seed_user, name="Refund", amount="30.00", income=True,
        )

        # The rows net to 0.00, so the whole line is the difference.
        accepted = _submit(
            seed_user, lines=[line], transactions=[out, back],
            residual="-500.00",
        )

        assert accepted.residual == Decimal("-500.00")
        assert _minted(seed_user)[0].estimated_amount == Decimal("500.00")

    def test_MIXED_signs_inside_a_group_stay_legal(self, app, db, seed_user):
        """A net deposit really is a gross income row less a deduction, so
        the check is over the two SUMS and never over a member."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2400.05",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        gross = a_transaction(
            seed_user, name="Gross Pay", amount="2500.00", income=True,
        )
        deduction = a_transaction(
            seed_user, name="Union Dues", amount="100.00",
        )

        accepted = _submit(
            seed_user, lines=[line], transactions=[gross, deduction],
            residual="0.05",
        )

        assert accepted.residual == Decimal("0.05")
        assert len(_minted(seed_user)) == 1


class TestAFigureThisAppCannotStoreIsRefused:
    """A door that SUMS is not bounded by the columns it sums.

    Every figure descends from a ``Numeric(12, 2)`` column, but a match may
    name up to 100 of them per side -- so the total, and the difference derived
    from it, can leave the domain any one of them lives in.  Reaching the
    database with one is ``NumericValueOutOfRange``, which is not a
    ``ValidationError`` and therefore escapes the per-item SAVEPOINT: it kills
    the WHOLE pass and every item that had landed beside it.  Reproduced by
    adversarial security review 2026-08-23.
    """

    @staticmethod
    def _at_the_ceiling(seed_user, count):
        """Stage *count* bank lines each at the money columns' maximum.

        Args:
            seed_user: The seeded user bundle.
            count: How many to stage.

        Returns:
            The lines.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        return [
            a_bank_line(
                seed_user, statement, amount="-9999999999.99",
                posted_on=bank_day, sequence_in_group=index,
            )
            for index in range(count)
        ]

    def test_a_sum_past_the_column_is_a_REFUSAL_and_not_a_500(
        self, app, db, seed_user,
    ):
        """Three lines at the ceiling, against one row.

        Staged as OUTFLOWS against an expense row, so the sign check passes
        and this arm is what fires.  Without the bound the sum reaches
        ``settled_amount`` as ``-29,999,999,999.97`` and dies at the database
        -- an unhandled 500 that takes every other item in the pass with it.
        """
        lines = self._at_the_ceiling(seed_user, 3)
        txn = a_transaction(seed_user, amount="1.00")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=lines, transactions=[txn],
                residual="-29999999997.97",
            )

        assert "larger than this app can record" in str(caught.value)
        assert txn.settled_amount is None

    def test_a_DIFFERENCE_past_the_column_is_refused_too(
        self, app, db, seed_user,
    ):
        """The other write the bound protects: the row this door mints.

        The bank side alone fits here; it is what the group leaves over that
        does not, so a bound on the sums alone would let this through.
        """
        lines = self._at_the_ceiling(seed_user, 2)
        one = a_transaction(seed_user, name="Power", amount="1.00")
        two = a_transaction(seed_user, name="Water", amount="1.00")

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=lines, transactions=[one, two],
                residual="-19999999997.98",
            )

        assert "larger than this app can record" in str(caught.value)
        assert not _minted(seed_user)


class TestTheSHAPESAGroupCanTake:
    """Members this arc's other row kinds contribute, and the name's edges.

    Every case here is one an adversarial mutation sweep found ungraded on
    2026-08-23: the tie-break between two same-day lines, the name's own
    length bound, a PURCHASE member, and a group straddling a pay period.
    """

    def test_a_PURCHASE_can_be_a_member_of_a_group_with_a_difference(
        self, app, db, seed_user,
    ):
        """The other matchable row kind, which no residual case had named.

        A purchase settles through a different verb from a transaction
        (``entry_service.update_entry``), so a group holding one exercises a
        path the transaction-only cases cannot.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-100.06", posted_on=bank_day,
        )
        envelope = a_transaction(
            seed_user, name="Groceries", amount="60.00", is_envelope=True,
        )
        # A purchase stores a positive figure; its CASH effect is the
        # negation (``_candidates.purchase_candidate``), so this contributes
        # -60.00 to the app side.
        purchase = a_purchase(
            seed_user, envelope, amount="60.00", purchased_on=bank_day,
        )
        other = a_transaction(seed_user, name="Power", amount="40.00")

        accepted = _submit(
            seed_user, lines=[line], transactions=[other],
            entries=[purchase], residual="-0.06",
        )

        assert accepted.residual == Decimal("-0.06")
        assert _minted(seed_user)[0].estimated_amount == Decimal("0.06")
        assert purchase.settled_on == bank_day

    def test_the_name_is_CUT_to_what_the_column_holds(
        self, app, db, seed_user,
    ):
        """``transactions.name`` is 200 characters and this door writes it.

        **Reached through the DESCRIPTION rather than the merchant**, and the
        difference is the point: ``bank_statement_lines.merchant`` is 100
        characters, so 22 + 100 always fits -- but ``merchant_label`` falls
        back to the 200-character description for a source that names no
        merchant, and 22 + 200 does not.  A test that reached for the merchant
        would have graded an unreachable branch and called the cut covered.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
            description="Statement of record " + "X" * 180, merchant=None,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        name = _minted(seed_user)[0].name
        assert len(name) == 200
        assert name.startswith("Statement difference: Statement of record XXX")

    def test_two_lines_on_ONE_day_break_the_tie_by_id(
        self, app, db, seed_user,
    ):
        """The name may not depend on which row a query happened to return.

        ``max`` over the posted day alone is satisfied by either line, so
        without the id the row's caption would change without the record
        changing.  Every other multi-line case here uses different days, which
        is why a sweep found this ungraded.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        first = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=bank_day,
            description="POINT OF SALE DEBIT LOWES", merchant="Lowe's",
        )
        second = a_bank_line(
            seed_user, statement, amount="-80.06", posted_on=bank_day,
            description="POINT OF SALE DEBIT WALMART", merchant="Walmart",
        )
        assert second.id > first.id, "the fixture must stage a real tie-break"
        one = a_transaction(seed_user, name="Power", amount="100.00")
        two = a_transaction(seed_user, name="Water", amount="80.00")

        _submit(
            seed_user, lines=[first, second], transactions=[one, two],
            residual="-0.06",
        )

        assert _minted(seed_user)[0].name == "Statement difference: Walmart"

    def test_a_group_STRADDLING_a_period_lands_by_the_day_it_POSTS_on(
        self, app, db, seed_user,
    ):
        """The match's own day decides, and the two candidates differ here.

        A residual has no budget clock -- it IS the movement -- so it belongs
        to the paycheck holding the day the match posts on, which is the
        LATEST of its lines.  Staged across a period boundary so that
        ``happened_on`` (the earliest stated day) would file it in the other
        paycheck, which is the mutation a sweep found nothing catching.
        """
        later = a_later_period(seed_user)
        statement = an_import(seed_user)
        earlier = a_bank_line(
            seed_user, statement, amount="-100.00",
            posted_on=seed_user["bootstrap_period"].end_date,
        )
        crossing = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=later.start_date,
        )
        one = a_transaction(seed_user, name="Power", amount="100.00")
        two = a_transaction(seed_user, name="Water", amount="80.00")

        accepted = _submit(
            seed_user, lines=[earlier, crossing], transactions=[one, two],
            residual="-0.06",
        )

        assert accepted.posts_on == later.start_date
        assert _minted(seed_user)[0].pay_period_id == later.id


class TestTheACTS_OWN_WRITES_CannotMoveAMemberUnderIt:
    """The identity holds BY CONSTRUCTION, and this is what makes that a fact.

    Both adversarial reviews of 2026-08-23 found the same hole independently:
    the two sides are measured before any settle verb runs, and settling a
    matched PURCHASE re-derives a SIBLING CC Payback's ``estimated_amount``
    through ``sync_entry_payback`` -- a row ``_reject_parent_and_its_own_purchase``
    cannot see, because it is the purchase's sibling under one envelope rather
    than its parent.  A first version of this step carried a comment asserting
    no settle verb could do that, which is the claim ``_scope`` and
    ``_resolve`` both record as MEASURED FALSE one tier up.
    """

    @staticmethod
    def _drifted_payback(seed_user):
        """Stage an envelope, a card entry, its drifted payback, and a debit.

        **Built the way ``credit_workflow._create_payback`` builds one**:
        ``ck_transactions_one_pricing_link`` admits at most one of
        ``template_id`` / ``transfer_id`` / ``credit_payback_for_id``, so a
        payback carrying a template is a row the database refuses -- and a
        fixture the app could not produce would grade an unreachable case.

        Args:
            seed_user: The seeded user bundle.

        Returns:
            ``(debit purchase, payback)``.
        """
        day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="300.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="50.00", description="Card",
            purchased_on=day, is_credit=True,
        )
        debit = a_purchase(
            seed_user, envelope, amount="25.00", description="Aldi",
            purchased_on=day,
        )
        payback = Transaction(
            account_id=seed_user["account"].id,
            template_id=None,
            pay_period_id=seed_user["bootstrap_period"].id,
            scenario_id=seed_user["scenario"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name="CC Payback: Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            # DRIFTED off the sum of its card entries, which is what an owner
            # correcting a projected payback to their card statement produces.
            # Settling the debit purchase is what snaps it back to 50.00.
            estimated_amount=Decimal("60.00"),
            credit_payback_for_id=envelope.id,
        )
        db.session.add(payback)
        db.session.flush()
        return debit, payback

    def test_a_group_whose_own_settle_moves_a_SIBLING_is_refused(
        self, app, db, seed_user,
    ):
        """One match naming both halves of the interaction.

        At submit time the two sides agree to the cent: `-25.00` of purchase
        plus `-60.00` of payback against an `-85.00` line.  Settling the
        purchase rewrites the payback to `-50.00`, so the members come to
        `-75.00` and the match no longer explains the line it named.  Without
        the post-write check this records a group that does not add up --
        silently, because every refusal it was tested against had already
        passed.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        debit, payback = self._drifted_payback(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-85.00", posted_on=day,
        )

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[payback],
                entries=[debit],
            )

        assert "moved one of its own rows" in str(caught.value)
        assert "-85.00" in str(caught.value)
        # Nothing survives the refusal: no match, and no minted row.
        assert not statement_match.review_set(a_scope(seed_user)).accepted
        assert not _minted(seed_user)

    def test_the_SAME_group_without_the_sibling_still_lands(
        self, app, db, seed_user,
    ):
        """The control: the check refuses drift, not groups.

        Without it, a check that refused everything would pass the case above
        and be indistinguishable from one that works.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        debit, _ = self._drifted_payback(seed_user)
        other = a_transaction(seed_user, name="Power", amount="60.00")
        line = a_bank_line(
            seed_user, statement, amount="-85.00", posted_on=day,
        )

        accepted = _submit(
            seed_user, lines=[line], transactions=[other], entries=[debit],
        )

        assert accepted.residual is None
        assert accepted.settled_count == 2


class TestTheReceiptNamesIt:
    """A receipt silent about a row this act CREATED would be false.

    The same argument ``repriced_count`` was added under 2026-08-22, when the
    panel said *"Nothing moved."* over a rewritten figure.
    """

    def test_the_minted_row_is_NOT_counted_as_settled(
        self, app, db, seed_user,
    ):
        """It is born on the bank's day, so counting it as a row the bank's
        evidence settled would claim work on a record that did not exist."""
        line, salary, allowance = _payroll(seed_user)

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert accepted.settled_count == 2

    def test_a_pass_that_ONLY_records_a_difference_did_not_move_nothing(
        self, app, db, seed_user,
    ):
        """``moved_nothing`` decides the panel's headline sentence.

        **Both members are staged ALREADY settled on the bank's own day, with
        the bank's own basis**, so ``_apply_day`` returns early for each and
        every day count is zero.  That is the whole point of the case: the
        recorded difference is then the ONLY thing the pass did, so a
        ``moved_nothing`` blind to it would render *"Nothing moved."* over a
        row this pass created -- the same false sentence ``repriced_count`` was
        added to stop in 2026-08-22.  A mutation sweep is what showed a first
        version of this case grading nothing: it mutated the two rows after
        staging them, and ``Transaction.status`` is a RELATIONSHIP that a flush
        does not refresh, so the door still saw them Projected and settled them.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
        )
        settled = {
            "status": StatusEnum.RECEIVED,
            "settled_on": bank_day,
            "settle_day_basis": SettledDayBasisEnum.OBSERVED,
        }
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True, **settled,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
            **settled,
        )
        scope = a_scope(seed_user)

        outcome = statement_match.apply_reviewed(
            statement_match.ReviewedBatch(
                consent=statement_match.Consent.TICKED,
                matches=(a_submission(
                    scope, lines=[line], transactions=[salary, allowance],
                    residual="0.05",
                ),),
                creations=(),
            ),
            scope,
        )

        assert outcome.refused == ()
        # Nothing about the two members moved: they already recorded the
        # bank's day, on the bank's own basis.
        assert (
            outcome.settled_count, outcome.corrected_count,
            outcome.redated_count, outcome.repriced_count,
        ) == (0, 0, 0, 0)
        assert outcome.residual_count == 1
        assert outcome.residual_total == Decimal("0.05")
        assert outcome.moved_nothing is False
        assert "recorded the +0.05 difference" in outcome.applied[0].summary

    def test_a_pass_ACCUMULATES_over_several_recorded_differences(
        self, app, db, seed_user,
    ):
        """The receipt is a pass total, not the last item's.

        Every other case here applies exactly ONE item, so ``+=`` and a plain
        assignment read identically -- which is how a mutation sweep found the
        accumulation ungraded.  Two groups, and the total is signed and netted
        because that is what ``residual_total`` claims to be.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        deposit = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        debit = a_bank_line(
            seed_user, statement, amount="-180.02", posted_on=bank_day,
        )
        power = a_transaction(seed_user, name="Power", amount="100.00")
        water = a_transaction(seed_user, name="Water", amount="80.00")
        scope = a_scope(seed_user)

        outcome = statement_match.apply_reviewed(
            statement_match.ReviewedBatch(
                consent=statement_match.Consent.TICKED,
                matches=(
                    a_submission(
                        scope, lines=[deposit],
                        transactions=[salary, allowance], residual="0.05",
                    ),
                    a_submission(
                        scope, lines=[debit], transactions=[power, water],
                        residual="-0.02",
                    ),
                ),
                creations=(),
            ),
            scope,
        )

        assert outcome.refused == ()
        assert outcome.residual_count == 2
        # +0.05 and -0.02 net to +0.03, which is what reached the buckets.
        assert outcome.residual_total == Decimal("0.03")
        assert len(_minted(seed_user)) == 2
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) == Decimal("-0.05")
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.EXPENSE,
        ) == Decimal("0.02")
