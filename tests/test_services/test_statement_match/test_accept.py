"""The ACCEPT door: what it refuses, and what it moves when it does not.

Plan step **bank_import:X-f6a-2**, rulings **R-FS**, **R-FP** and **R-FV**.
This is the only door in the arc that moves money, so every one of its
refusals is a FIRING CONTROL here -- written to fail if the refusal were
deleted, which is the standard ``docs/plans/verification.md`` sets and which
this project has twice measured the absence of.

**What the door promises, and what each class below pins:**

* a group's two sides SUM, or nothing is written and the difference is named;
* every member row takes the bank's day -- settling a Projected row and
  correcting a settled one;
* it does NOT write ``reconciled_by_id`` (ruling **R-FV**) and it RELEASES any
  link it moves a day out from under;
* an id outside the owner's own candidate set is refused, not skipped;
* a release restores the QUESTION and leaves the days alone.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import statement_match
from app.services.statement_match import MatchSubmission

from ._builders import a_bank_line, a_purchase, a_transaction, an_import


def _submit(seed_user, lines=(), transactions=(), entries=()):
    """Accept a match naming exactly these subjects.

    Args:
        seed_user: The seeded user bundle.
        lines: Bank line rows.
        transactions: Transaction rows.
        entries: Purchase rows.

    Returns:
        The :class:`~app.services.statement_match.AcceptedMatch`.
    """
    return statement_match.accept_match(MatchSubmission(
        owner_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        line_ids=frozenset(line.id for line in lines),
        transaction_ids=frozenset(txn.id for txn in transactions),
        entry_ids=frozenset(entry.id for entry in entries),
    ))


class TestABalancedMatchIsRecorded:
    """The ordinary one-line-one-row case."""

    def test_the_row_takes_the_banks_day(self, app, db, seed_user):
        """The correction this whole arc exists to make.

        Measured on the developer's own statement: of 58 lines an exact-amount
        predicate pairs uniquely with a row, only 23 carry the day the app had
        recorded.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=3),
        )

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on == bank_day
        assert accepted.corrected_count == 1
        assert accepted.settled_count == 0
        assert accepted.posts_on == bank_day

    def test_a_projected_row_is_settled(self, app, db, seed_user):
        """The bank is evidence the money moved, so the row says so.

        11 rows inside the developer's own statement span had never been
        marked as having happened.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on == bank_day
        assert txn.status.is_settled
        assert accepted.settled_count == 1
        assert accepted.corrected_count == 0

    def test_a_row_already_on_the_banks_day_counts_as_neither(
        self, app, db, seed_user,
    ):
        """Confirming is not correcting, and the report must not claim it is."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert (accepted.settled_count, accepted.corrected_count) == (0, 0)

    def test_the_match_is_recorded_with_one_member_per_subject(
        self, app, db, seed_user,
    ):
        """The relation is what makes a re-import stop re-proposing it."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        members = (
            db.session.query(StatementMatchMember)
            .filter(StatementMatchMember.match_id == accepted.match_id)
            .all()
        )
        assert len(members) == 2
        assert {m.bank_statement_line_id for m in members} == {line.id, None}
        assert {m.transaction_id for m in members} == {txn.id, None}


class TestTheGroupMustSum:
    """The developer's ruling of 2026-08-17, and finding **N-299**'s own data."""

    def test_a_five_cent_shortfall_is_refused(self, app, db, seed_user):
        """The payroll shape: the bank paid more than the app's rows say.

        6 of 16 payroll deposits on the developer's own statement sit
        `$0.05`-`$0.06` apart from what the app holds.  A tolerance would
        absorb exactly the defect the matcher is the first instrument able to
        see.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="2573.43")
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
            )

        assert "0.05" in str(caught.value)
        assert salary.settled_on is None
        assert allowance.settled_on is None

    def test_a_group_that_sums_is_accepted(self, app, db, seed_user):
        """The control: the same shape, correct to the cent."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.38", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
        )

        assert accepted.settled_count == 2
        assert salary.settled_on == bank_day
        assert allowance.settled_on == bank_day

    def test_every_member_takes_the_LATEST_bank_day(self, app, db, seed_user):
        """N lines, one row: the row is not wholly moved until the last posts.

        Taking the earliest would let a balance asserted between the two absorb
        money that had not all left the account.
        """
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        last = first + timedelta(days=2)
        line_a = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
        )
        line_b = a_bank_line(
            seed_user, statement, amount="-80.00", posted_on=last,
        )
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(
            seed_user, lines=[line_a, line_b], transactions=[txn],
        )

        assert accepted.posts_on == last
        assert txn.settled_on == last


class TestEveryOtherRefusalFires:
    """Each of these is reachable from a stale page, and each writes nothing."""

    def test_a_match_with_no_row_is_refused(self, app, db, seed_user):
        """One thing is not a match."""
        line = a_bank_line(seed_user, an_import(seed_user))

        with pytest.raises(ValidationError, match="at least one"):
            _submit(seed_user, lines=[line])

    def test_a_match_with_no_line_is_refused(self, app, db, seed_user):
        """The other half of the same rule."""
        txn = a_transaction(seed_user, amount="180.00")

        with pytest.raises(ValidationError, match="at least one"):
            _submit(seed_user, transactions=[txn])

    def test_an_envelope_and_its_own_purchase_is_refused(
        self, app, db, seed_user,
    ):
        """It would count the same money twice, and no CHECK can see it.

        An envelope's cash leg already INCLUDES its outstanding purchases, so
        naming both sums that purchase in two terms.
        """
        statement = an_import(seed_user)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(seed_user, envelope, amount="25.00")
        line = a_bank_line(seed_user, statement, amount="-125.00")

        with pytest.raises(ValidationError, match="count the same money twice"):
            _submit(
                seed_user, lines=[line],
                transactions=[envelope], entries=[purchase],
            )

    def test_an_envelope_matched_SEPARATELY_from_its_purchase_is_refused(
        self, app, db, seed_user,
    ):
        """The cross-match half, and the one that actually moves money.

        **Within one match the two sides are priced together and refuse
        together; across two, each balances on its own and the second
        FALSIFIES the first.**  Measured on a production clone by adversarial
        financial review 2026-08-17: envelope 2280 prices at `-265.69` with its
        four unposted purchases included, its purchase 78 at `-18.64`; matching
        the envelope first and the purchase second stamps the purchase's
        posting day, dropping the envelope's leg to `-247.05` -- so two matched
        line-sets worth `-284.33` end up backed by `-265.69` of ledger and the
        projected balance reads `$18.64` HIGH.  The hand-build form lists an
        envelope and its purchases side by side, so it is two clicks.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        # An ENVELOPE settles at sum(entries) (``settles_from_entries``), so
        # its cash leg is that sum rather than its estimate -- two purchases,
        # so the envelope's own figure is distinguishable from either of them.
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        a_purchase(
            seed_user, envelope, amount="30.00", description="Aldi",
            purchased_on=bank_day,
        )
        envelope_line = a_bank_line(
            seed_user, statement, amount="-55.00", posted_on=bank_day,
        )
        purchase_line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            sequence_in_group=1,
        )
        _submit(seed_user, lines=[envelope_line], transactions=[envelope])

        with pytest.raises(ValidationError, match="already accepted"):
            _submit(seed_user, lines=[purchase_line], entries=[purchase])

        assert purchase.settled_on is None

    def test_a_purchase_matched_SEPARATELY_from_its_envelope_is_refused(
        self, app, db, seed_user,
    ):
        """The same clash approached from the other side.

        Asked in both orders because the guard reads two relations -- a
        submitted purchase against already-matched parents, and a submitted
        envelope against already-matched purchases -- and one of them being
        right proves nothing about the other.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        a_purchase(
            seed_user, envelope, amount="30.00", description="Aldi",
            purchased_on=bank_day,
        )
        purchase_line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )
        # The envelope books only what its purchases did NOT (ruling R-FM), so
        # once the 25.00 purchase has posted its close is worth 30.00.
        envelope_line = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=bank_day,
            sequence_in_group=1,
        )
        _submit(seed_user, lines=[purchase_line], entries=[purchase])

        with pytest.raises(ValidationError, match="already accepted"):
            _submit(seed_user, lines=[envelope_line], transactions=[envelope])

    def test_a_cancelled_row_is_refused(self, app, db, seed_user):
        """Not money this account moved, so not a candidate and not matchable."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(
            seed_user, amount="180.00", status=StatusEnum.CANCELLED,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[line], transactions=[txn])

    def test_a_card_purchase_is_refused(self, app, db, seed_user):
        """A card purchase never touches checking, so no statement shows it."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-25.00")
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", is_credit=True,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[line], entries=[purchase])

    def test_an_unknown_line_is_refused(self, app, db, seed_user):
        """A refusal rather than a silent skip: this door names rows on purpose."""
        txn = a_transaction(seed_user, amount="180.00")

        with pytest.raises(ValidationError, match="no longer on this account"):
            statement_match.accept_match(MatchSubmission(
                owner_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                line_ids=frozenset({999999}),
                transaction_ids=frozenset({txn.id}),
                entry_ids=frozenset(),
            ))

    def test_a_second_match_on_one_LINE_is_refused(self, app, db, seed_user):
        """The line twin of the row guard below, and it was missing.

        ``uq_statement_match_members_line`` refuses the second act either way,
        so nothing could be corrupted -- but it arrives as an
        ``IntegrityError`` AFTER ``_apply_day`` has already moved a settle day,
        which reaches the user as "Something went wrong" and logs a traceback
        at ERROR for an ordinary stale page.  The hand-build form renders a
        checkbox per unmatched line, so one tab submitting a line another tab
        just matched is two clicks.  Found by adversarial security review
        2026-08-17.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        first = a_transaction(seed_user, name="Electric", amount="180.00")
        second = a_transaction(seed_user, name="Water", amount="180.00")
        _submit(seed_user, lines=[line], transactions=[first])

        with pytest.raises(ValidationError, match="already matched"):
            _submit(seed_user, lines=[line], transactions=[second])

        assert second.settled_on is None

    def test_a_second_match_on_one_row_is_refused(self, app, db, seed_user):
        """A matched row leaves the candidate set, so it cannot be claimed twice."""
        statement = an_import(seed_user)
        first = a_bank_line(seed_user, statement, sequence_in_group=0)
        second = a_bank_line(seed_user, statement, sequence_in_group=1)
        txn = a_transaction(seed_user, amount="180.00")
        _submit(seed_user, lines=[first], transactions=[txn])

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[second], transactions=[txn])


class TestTheClearingLinkIsNotWritten:
    """Ruling **R-FV**: identity is stored, absorption is derived."""

    def test_no_member_gains_a_clearing_link(self, app, db, seed_user):
        """A bank line names no ``account_anchor_history`` row, so nor does this."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")

        _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.reconciled_by_id is None

    def test_moving_a_day_RELEASES_an_existing_link(self, app, db, seed_user):
        """The bank has just contradicted the day that link was recorded on.

        The release is the settle doors' own rule (``status_seam``); this pins
        that the accept door goes THROUGH them rather than writing the column,
        which is what makes the two facts unable to disagree.
        """
        from app.models.account import AccountAnchorHistory  # local: this test

        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        anchor = (
            db.session.query(AccountAnchorHistory)
            .filter_by(account_id=seed_user["account"].id)
            .first()
        )
        assert anchor is not None, "create_account writes the opening assertion"
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=2),
        )
        txn.reconciled_by_id = anchor.id
        db.session.flush()
        line = a_bank_line(seed_user, statement, posted_on=bank_day)

        _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on == bank_day
        assert txn.reconciled_by_id is None


class TestReleasingAMatch:
    """The repair door finding **N-302** says a refusal owes."""

    def test_it_deletes_the_act_and_its_members(self, app, db, seed_user):
        """The bank lines become unexplained again."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")
        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        released = statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )

        assert released == 2
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.query(StatementMatchMember).count() == 0

    def test_it_does_NOT_put_the_day_back(self, app, db, seed_user):
        """The bank is still the best record of when that money moved.

        Reverting the correction to tidy a relation would throw away the fact
        and keep the bookkeeping.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=4),
        )
        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )

        assert txn.settled_on == bank_day

    def test_the_row_is_matchable_again(self, app, db, seed_user):
        """What a release restores is the QUESTION."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")
        accepted = _submit(seed_user, lines=[line], transactions=[txn])
        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )

        again = _submit(seed_user, lines=[line], transactions=[txn])

        assert again.match_id != accepted.match_id

    def test_another_owners_match_is_refused(self, app, db, seed_user):
        """404-for-both, as a refusal because this door names one act."""
        with pytest.raises(ValidationError, match="no longer there"):
            statement_match.release_match(
                999999, seed_user["user"].id, seed_user["account"].id,
            )


class TestAPurchaseIsMatchableToo:
    """A purchase is a cash movement of its own (plan step ``balance:X-f3b``)."""

    def test_a_purchase_takes_the_banks_posting_day(self, app, db, seed_user):
        """The 267 card-swipe lines on the developer's statement are these."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.settled_on == bank_day
        assert accepted.settled_count == 1


class TestATransferShadowIsMatchedThroughItsService:
    """The kind the bank shows most, and the one that must not be touched directly.

    **13 of 22 non-swipe one-to-one matches on the developer's own statement are
    transfer shadows** -- the mortgage transfer, the Van Loan payment, the money
    market moves -- and 9 of those carry a day the bank corrects.  So this is the
    ordinary case rather than an exotic one, and it is the case that must go
    through ``transfer_service``: ``CLAUDE.md``'s transfer invariant 4 admits no
    direct mutation of a shadow, and ``transaction_service.settle_transaction``
    REFUSES one outright.
    """

    @staticmethod
    def _a_transfer_shadow(db, seed_user, *, amount="75.00", settled=False):
        """Return the EXPENSE leg of a transfer off the seeded checking account.

        Built through ``transfer_service.create_transfer``, the sole creation
        chokepoint, so the pair and its invariants are the real ones.

        Args:
            db: The session fixture.
            seed_user: The seeded user bundle.
            amount: The transfer amount.
            settled: Whether to settle the pair first, so the match CORRECTS a
                day rather than settling one.

        Returns:
            The shadow :class:`~app.models.transaction.Transaction` on checking.
        """
        from app.models.account import Account  # local: this class only
        from app.models.transaction import Transaction
        from app.services import account_service, transfer_service
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
        if settled:
            transfer_service.settle_transfer(
                transfer.id, seed_user["user"].id,
                settled_on=seed_user["bootstrap_period"].start_date
                + timedelta(days=5),
            )
        db.session.flush()
        assert isinstance(destination, Account)
        return (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id == transfer.id,
                Transaction.account_id == seed_user["account"].id,
            )
            .one()
        )

    def test_a_projected_shadow_settles_through_the_transfer_service(
        self, app, db, seed_user,
    ):
        """Both legs and the parent move in one call, which is invariant 3."""
        from app.models.transaction import Transaction  # local: this class only

        shadow = self._a_transfer_shadow(db, seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transactions=[shadow])

        assert accepted.settled_count == 1
        assert shadow.settled_on == bank_day
        assert shadow.status.is_settled
        sibling = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id == shadow.transfer_id,
                Transaction.id != shadow.id,
            )
            .one()
        )
        assert sibling.status.is_settled

    def test_a_settled_shadows_day_is_CORRECTED(self, app, db, seed_user):
        """The majority case on real data, and the one a settle verb cannot do.

        ``transfer_service.settle_transfer`` is an idempotent no-op on a
        transfer already in the settled band -- it writes nothing and reports
        False -- so a door that only knew that verb would silently fail to
        correct 9 of the developer's own 13 shadow matches.  The dispatch takes
        ``update_transfer`` here instead, and this is the control over it.
        """
        shadow = self._a_transfer_shadow(db, seed_user, settled=True)
        bank_day = seed_user["bootstrap_period"].start_date
        assert shadow.settled_on != bank_day
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transactions=[shadow])

        assert accepted.corrected_count == 1
        assert accepted.settled_count == 0
        assert shadow.settled_on == bank_day

    def test_a_shadow_whose_PARENT_is_gone_is_not_matchable(
        self, app, db, seed_user,
    ):
        """Not money this account owes, and pricing one asks about a gone row.

        Unreachable through today's doors -- ``delete_transfer(soft=True)``
        marks the transfer AND both shadows, and production carries 0 of these
        -- so this plants the state directly.  A scope that is correct only
        because every writer keeps a convention is a contract nobody can see.
        """
        from app.models.transfer import Transfer  # local: this test only

        shadow = self._a_transfer_shadow(db, seed_user)
        parent = db.session.get(Transfer, shadow.transfer_id)
        parent.is_deleted = True
        db.session.flush()
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[line], transactions=[shadow])


class TestAnAcceptedMatchStopsAgreeingWhenItStopsHolding:
    """``AcceptedGroup.agrees`` asks three questions, and a first draft asked one.

    **A CASCADE can falsify a match without touching a single day**, which is
    what an adversarial design review measured on 2026-08-17: deleting a
    purchase or destroying a pay period removes that member silently, and a
    day-only test then reports a group explaining less than it claims -- or,
    when every row goes, nothing at all -- as still agreeing with the bank.
    A SOFT delete is worse, because it does not cascade at all: the row keeps
    its ``settled_on`` and contributes zero to every balance, so only the SUM
    can see it has gone.
    """

    @staticmethod
    def _accepted_pair(db, seed_user):
        """Accept a two-row group and return its rows and the review set."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.38", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        _submit(seed_user, lines=[line], transactions=[salary, allowance])
        return salary, allowance

    def _groups(self, seed_user):
        """Return the accepted groups the screen would render."""
        return statement_match.review_set(
            seed_user["user"].id, seed_user["account"].id,
        ).accepted

    def test_it_agrees_while_it_holds(self, app, db, seed_user):
        """The control, without which every arm below could pass vacuously."""
        self._accepted_pair(db, seed_user)

        groups = self._groups(seed_user)

        assert len(groups) == 1
        assert groups[0].agrees is True

    def test_a_hand_moved_day_stops_it_agreeing(self, app, db, seed_user):
        """The owner contradicted the bank, and the screen says so."""
        salary, _ = self._accepted_pair(db, seed_user)
        salary.settled_on = salary.settled_on + timedelta(days=1)
        db.session.flush()

        assert self._groups(seed_user)[0].agrees is False

    def test_a_SOFT_DELETED_member_stops_it_agreeing(
        self, app, db, seed_user,
    ):
        """The case no test over DAYS can see.

        A soft-deleted row keeps its recorded day, so every ``agrees`` test
        that compared days alone reported this group as still explaining the
        bank's `$2,573.38` -- while the row contributes `$0.00` to any balance
        and the two sides no longer sum.
        """
        salary, _ = self._accepted_pair(db, seed_user)
        assert self._groups(seed_user)[0].agrees is True
        salary.is_deleted = True
        db.session.flush()

        group = self._groups(seed_user)[0]

        assert group.agrees is False
        assert all(row.settled_on == group.posts_on for row in group.rows), (
            "the days are untouched -- which is why a day-only test was blind"
        )


class TestAMatchStopsHoldingWhenAPURCHASELEAVESTHEACCOUNT:
    """A purchase flipped to CARD moves no cash through this account at all."""

    def test_flipping_a_matched_purchase_to_card_breaks_agreement(
        self, app, db, seed_user,
    ):
        """It keeps its day, so only the SUM can see it has gone.

        ``update_entry`` supports the flip and releases the clearing link for
        it; the purchase keeps ``settled_on`` throughout.  A valuation reading
        the magnitude alone therefore reported the match as still explaining
        the bank's figure while the money had left through the card instead.
        Found by adversarial financial review 2026-08-17.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )
        _submit(seed_user, lines=[line], entries=[purchase])
        before = statement_match.review_set(
            seed_user["user"].id, seed_user["account"].id,
        ).accepted
        assert before[0].agrees is True

        purchase.is_credit = True
        db.session.flush()

        after = statement_match.review_set(
            seed_user["user"].id, seed_user["account"].id,
        ).accepted
        assert after[0].agrees is False
        assert after[0].rows[0].settled_on == bank_day, (
            "the day is untouched -- which is why a day-only test was blind"
        )
