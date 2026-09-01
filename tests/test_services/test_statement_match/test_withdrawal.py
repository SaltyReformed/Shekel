"""A row leaving the books withdraws the matches naming it.

Plan step **bank_import:X-gb**, developer ruling 2026-08-25.  Wiring
``DELETE /transactions/<id>`` to a surface (finding **N-344**) made a state
reachable from a control that had only ever been reachable from a script: a
match holding a bank line and nothing else, with the line still counted as
explained (``matched_subjects`` reads the member rows) until the owner notices
the act flagged on the review screen and presses Undo.

**The screen's flag is NOT removed by this, and the two answer different
questions.**  :attr:`~app.services.statement_match.AcceptedGroup.agrees` asks
whether an act still HOLDS -- a day moved by hand, a purchase flipped to card,
a member soft-deleted -- and those are acts that want re-reviewing.  A row
deleted deliberately is not one of them: there is nothing left to re-review, so
the assertion is withdrawn where the row leaves and the line is unexplained
again in the same request.

It lives in this package's test folder rather than beside ``match_withdrawal``
because every fixture it needs -- an account, an import, a line, an accepted
act -- is :mod:`._builders`, and the SUBJECT under test is what a match means
once its subject has gone.

**Five doors call the rule and all five are exercised here.**  The rule does NOT
claim to be every door and a first draft did: an adversarial review measured
``routes/templates/crud``'s hard-delete reaching the same state from a shipped
button, and three more bulk paths beside it.  So the INVARIANT is a predicate
in the reader (``_candidates.act_still_names_a_row``) which every door obeys
without knowing it exists, and :class:`TestTheInvariantHoldsThroughADoorThatDoesNotCallTheRule`
grades that; what the five doors add is the CLEANUP and the DISCLOSURE.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.ref import AccountType
from app.models.statement_match import (
    StatementMatch,
    StatementMatchCreation,
)
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.statement_match import (
    NewEnvelope,
    PurchaseCreation,
)
from app.services import (
    account_service,
    credit_workflow,
    entry_service,
    match_withdrawal,
    statement_match,
    transaction_service,
    transfer_service,
)

# Pylint: protected-access -- ``MintedEnvelopes`` is an internal collaboration
# between two PRIVATE modules of this package and has no importer outside it;
# a test for the module reaches into it, which is the allowance every sibling
# here takes (see ``test_release.py``).
from app.services.statement_match import _create  # pylint: disable=protected-access

from tests._test_helpers import open_books_before_the_first_assertion
from ._builders import (
    a_bank_line,
    a_bars,
    a_later_period,
    a_purchase,
    a_scope,
    a_submission,
    a_transaction,
    accepted_acts,
    an_answers,
    an_import,
)


def _submit(
    seed_user, lines=(), transactions=(), entries=(), residual=None,
):
    """Accept a match naming exactly these subjects."""
    scope = a_scope(seed_user)
    return statement_match.accept_match(
        a_submission(
            scope, lines=lines, transactions=transactions, entries=entries,
            residual=residual,
        ),
        scope,
    )


def _matched_line_ids(seed_user):
    """Return the bank lines this account still counts as explained."""
    return statement_match.matched_subjects(seed_user["account"].id).lines


def _a_savings_account(seed_user):
    """Create the far side a transfer needs.

    ``seed_user`` builds ONE account, so a transfer test builds its own second
    one -- through the canonical factory, so the account carries the
    origination assertion and ledger pairing a production account has and the
    transfer's posting reconcile has somewhere real to post.
    """
    savings_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("2000.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        ),
    )
    db.session.flush()
    # Its BOOKS open the day before that assertion (plan step X-f3c-2b,
    # ruling **R-HG**): an opening equity is the closing balance for its own
    # day, and this suite's bank lines post on ``bootstrap_period.start_date``
    # itself.  Same shape ``tests/conftest.py``'s own factories take.
    open_books_before_the_first_assertion(db.session, account)
    return account


def _a_transfer(seed_user):
    """Create a checking -> savings transfer and its two shadows."""
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=_a_savings_account(seed_user).id,
            pay_period_id=seed_user["bootstrap_period"].id,
            scenario_id=seed_user["scenario"].id,
            amount=Decimal("500.00"),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=seed_user["categories"]["Groceries"].id,
            notes=None,
            transfer_template_id=None,
            name="Transfer to Savings",
            due_date=None,
        ),
    )
    db.session.flush()
    return xfer


def _delete(seed_user, txn):
    """Delete *txn* through the verb every delete door shares."""
    outcome = transaction_service.delete_transaction(txn, seed_user["user"].id)
    db.session.flush()
    return outcome


class TestTheReadAndTheWriteAgree:
    """What the confirm dialog prints is what the press performs.

    One derivation with two readers, which is the shape
    ``statement_match.planned_removals`` already has one door over: two
    spellings would let the dialog promise a line back and the press keep it.
    """

    def test_a_row_no_act_names_withdraws_nothing(
        self, app, db, seed_user,
    ):
        """The control, without which every arm below could pass vacuously."""
        txn = a_transaction(seed_user, name="Electricity")
        db.session.flush()

        pending = match_withdrawal.pending_for_rows([txn])

        assert pending.matches == 0
        assert pending.lines == ()
        assert pending.frees_a_line is False

    def test_the_pending_read_names_the_line_the_press_frees(
        self, app, db, seed_user,
    ):
        """The dialog names the DAY and the FIGURE, not a count.

        A control that destroys records and says "1 bank line" over a `$793.23`
        ACH payment is the *"Nothing moved."* sentence this arc has already
        shipped once.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-178.32", posted_on=day,
            description="ACH DEBIT GEICO PREM COLL",
        )
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32", template=False,
        )
        _submit(seed_user, lines=[line], transactions=[txn])

        pending = match_withdrawal.pending_for_rows([txn])

        assert pending.matches == 1
        assert pending.frees_a_line is True
        assert len(pending.lines) == 1
        freed = pending.lines[0]
        assert freed.line_id == line.id
        assert freed.posted_on == day
        assert freed.amount == Decimal("-178.32")
        assert "GEICO" in freed.description

        performed = _delete(seed_user, txn).withdrawn

        assert performed == pending, (
            "the dialog and the door must derive the same withdrawal"
        )

    def test_reading_writes_nothing(self, app, db, seed_user):
        """It runs on every popover open, so it may not touch the act."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-178.32")
        txn = a_transaction(seed_user, name="Geico", amount="178.32")
        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        match_withdrawal.pending_for_rows([txn])
        db.session.flush()

        assert db.session.get(StatementMatch, accepted.match_id) is not None
        assert line.id in _matched_line_ids(seed_user)


class TestTheDialogTheCardRenders:
    """What the owner reads before pressing Delete.

    Asserted through the ROUTE that builds the card, not by calling the read
    directly: the figures reach the dialog through the render context, and a
    context key the route forgets to publish is exactly the failure a
    service-level test cannot see (a Jinja ``Undefined`` answers silently).
    """

    def test_the_dialog_names_the_bank_line_the_delete_frees(
        self, app, db, seed_user, auth_client,
    ):
        """By day and by figure, because a COUNT is not a disclosure."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-178.32", posted_on=day,
            description="ACH DEBIT GEICO PREM COLL",
        )
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32", template=False,
        )
        _submit(seed_user, lines=[line], transactions=[txn])
        db.session.commit()

        html = auth_client.get(
            f"/transactions/{txn.id}/full-edit",
        ).data.decode()

        assert "withdraws 1 accepted match" in html
        assert "1 bank line is unexplained again" in html
        assert day.strftime("%-m/%-d") in html
        assert "-$178.32" in html
        assert "GEICO" in html

    def test_an_unmatched_row_says_nothing_about_bank_lines(
        self, app, db, seed_user, auth_client,
    ):
        """The control, without which the arm above could pass on any row."""
        txn = a_transaction(seed_user, name="Geico", amount="178.32")
        db.session.commit()

        html = auth_client.get(
            f"/transactions/{txn.id}/full-edit",
        ).data.decode()

        assert "Delete this row" in html
        assert "unexplained again" not in html


class TestEveryDoorThatRemovesARowWithdrawsItsMatches:
    """The census, asserted rather than stated in a docstring."""

    @staticmethod
    def _matched_row(seed_user, **kwargs):
        """Return an accepted (line, transaction) pair on this account."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-178.32")
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            **{"template": False, **kwargs},
        )
        _submit(seed_user, lines=[line], transactions=[txn])
        return line, txn

    def test_the_transaction_delete_verb_frees_the_line(
        self, app, db, seed_user,
    ):
        """An AD-HOC row leaves the table, and the line is unexplained again."""
        line, txn = self._matched_row(seed_user)
        assert line.id in _matched_line_ids(seed_user)

        outcome = _delete(seed_user, txn)

        assert outcome.soft is False
        assert outcome.withdrawn.matches == 1
        assert line.id not in _matched_line_ids(seed_user)

    def test_the_purchase_door_frees_its_line_and_leaves_the_parent(
        self, app, db, seed_user,
    ):
        """Removing one purchase says nothing about the envelope's own match."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=day,
        )
        kept = a_purchase(
            seed_user, envelope, amount="10.00", purchased_on=day,
        )
        purchase_line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=day,
        )
        kept_line = a_bank_line(
            seed_user, statement, amount="-10.00", posted_on=day,
        )
        _submit(seed_user, lines=[purchase_line], entries=[purchase])
        _submit(seed_user, lines=[kept_line], entries=[kept])

        entry_service.delete_entry(purchase.id, seed_user["user"].id)
        db.session.flush()

        matched = _matched_line_ids(seed_user)
        assert purchase_line.id not in matched
        assert kept_line.id in matched, (
            "the sibling purchase still explains its own line"
        )

    def test_undo_CC_frees_a_matched_paybacks_line(
        self, app, db, seed_user,
    ):
        """A payback a bank line named stops existing when Credit is reverted.

        Measured on the developer's own dev database at 4 matched paybacks,
        every one of them reachable from the Undo CC button on the grid card.
        """
        statement = an_import(seed_user)
        source = a_transaction(
            seed_user, name="Rogue Equipment", amount="200.00", template=False,
        )
        db.session.flush()
        a_later_period(seed_user)
        credit_workflow.mark_as_credit(source.id, seed_user["user"].id)
        db.session.flush()
        payback = credit_workflow.get_active_payback(source.id)
        assert payback is not None
        line = a_bank_line(seed_user, statement, amount="-200.00")
        _submit(seed_user, lines=[line], transactions=[payback])
        assert line.id in _matched_line_ids(seed_user)

        credit_workflow.delete_payback_on_credit_revert(
            source, seed_user["user"].id,
        )
        db.session.flush()

        assert line.id not in _matched_line_ids(seed_user)

    def test_deleting_a_credit_source_frees_its_paybacks_line(
        self, app, db, seed_user,
    ):
        """The payback goes down with its source, and its match with it."""
        statement = an_import(seed_user)
        source = a_transaction(
            seed_user, name="Rogue Equipment", amount="200.00", template=False,
        )
        db.session.flush()
        a_later_period(seed_user)
        credit_workflow.mark_as_credit(source.id, seed_user["user"].id)
        db.session.flush()
        payback = credit_workflow.get_active_payback(source.id)
        line = a_bank_line(seed_user, statement, amount="-200.00")
        _submit(seed_user, lines=[line], transactions=[payback])

        _delete(seed_user, source)

        assert line.id not in _matched_line_ids(seed_user)

    def test_deleting_a_transfer_frees_both_shadows_lines(
        self, app, db, seed_user,
    ):
        """A transfer's two legs move together, and so do their matches.

        Measured on the developer's own dev database at 16 matched shadows.
        """
        statement = an_import(seed_user)
        xfer = _a_transfer(seed_user)
        shadow = (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer.id, account_id=seed_user["account"].id)
            .one()
        )
        line = a_bank_line(seed_user, statement, amount="-500.00")
        _submit(seed_user, lines=[line], transactions=[shadow])
        assert line.id in _matched_line_ids(seed_user)

        transfer_service.delete_transfer(
            xfer.id, seed_user["user"].id, soft=False,
        )
        db.session.flush()

        assert line.id not in _matched_line_ids(seed_user)


class TestAnActIsWITHDRAWNONLYWhenItLosesItsLastRow:
    """The narrowing two adversarial reviews forced (2026-08-25).

    A first build withdrew on the loss of ANY member, which destroyed an act
    that was still two-thirds true and silently un-matched the survivors.
    Partial loss is what ``AcceptedGroup.agrees`` is for; this writer fires
    only where that flag has nothing left to re-review.
    """

    @staticmethod
    def _group_of_three(seed_user):
        """Accept one line against three rows and return them."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-300.00", posted_on=day,
        )
        rows = [
            a_transaction(
                seed_user, name=f"Row {n}", amount="100.00", template=False,
            )
            for n in range(3)
        ]
        _submit(seed_user, lines=[line], transactions=rows)
        return line, rows

    def test_losing_ONE_row_of_a_group_keeps_the_act(
        self, app, db, seed_user,
    ):
        """The other two rows still explain part of the movement."""
        line, rows = self._group_of_three(seed_user)

        outcome = _delete(seed_user, rows[0])

        assert outcome.withdrawn.matches == 0
        assert line.id in _matched_line_ids(seed_user), (
            "two rows still name it; the amber flag is what re-reviews this"
        )
        groups = accepted_acts(seed_user)
        assert len(groups) == 1
        assert groups[0].agrees is False, (
            "the sides no longer sum, which is what the flag is for"
        )

    def test_losing_the_LAST_row_withdraws_the_act(
        self, app, db, seed_user,
    ):
        """Now there is nothing left to re-review, so the line comes back."""
        line, rows = self._group_of_three(seed_user)
        _delete(seed_user, rows[0])
        _delete(seed_user, rows[1])
        assert line.id in _matched_line_ids(seed_user)

        outcome = _delete(seed_user, rows[2])

        assert outcome.withdrawn.matches == 1
        assert line.id not in _matched_line_ids(seed_user)
        assert not accepted_acts(seed_user)

    def test_a_SOFT_delete_leaves_the_act_standing(
        self, app, db, seed_user,
    ):
        """A flag change cascades no member, so the act still names its row.

        It is also the answer the transfer archive path needs: that soft delete
        is UNDONE by a shipped button (``transfers.templates`` un-archives
        through ``restore_transfer``), and a withdrawal there would destroy an
        accepted act the restore cannot put back.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-178.32")
        txn = a_transaction(seed_user, name="Geico", amount="178.32")
        _submit(seed_user, lines=[line], transactions=[txn])

        outcome = _delete(seed_user, txn)

        assert outcome.soft is True
        assert outcome.withdrawn.matches == 0
        assert line.id in _matched_line_ids(seed_user)
        assert accepted_acts(seed_user)[0].agrees is False, (
            "the row contributes nothing now, so the SUM says so"
        )


class TestTheDialogCoversEVERYTHINGThePressRemoves:
    """One derivation over one row set, measured rather than asserted in prose.

    A first build read the ROW alone while the press also tore down its live CC
    payback chain: an adversarial review measured a `$200.00` card payment
    silently un-explained, over a dialog naming no bank line at all and a log
    line reading ``matches_withdrawn=0``.
    """

    def test_the_preview_covers_a_matched_payback_the_press_takes(
        self, app, db, seed_user, auth_client,
    ):
        """Deleting a Credit source takes its payback -- and its payback's match."""
        statement = an_import(seed_user)
        source = a_transaction(
            seed_user, name="Rogue Equipment", amount="200.00", template=False,
        )
        db.session.flush()
        a_later_period(seed_user)
        credit_workflow.mark_as_credit(source.id, seed_user["user"].id)
        db.session.flush()
        payback = credit_workflow.get_active_payback(source.id)
        line = a_bank_line(seed_user, statement, amount="-200.00")
        _submit(seed_user, lines=[line], transactions=[payback])
        db.session.commit()

        preview = transaction_service.preview_deletion(source)

        assert preview.paybacks == (payback.name,)
        assert preview.withdrawn.matches == 1
        assert [freed.line_id for freed in preview.withdrawn.lines] == [line.id]

        html = auth_client.get(
            f"/transactions/{source.id}/full-edit",
        ).data.decode()
        assert "unexplained again" in html
        assert payback.name in html

        performed = _delete(seed_user, source)

        assert performed.withdrawn == preview.withdrawn
        assert performed.paybacks == preview.paybacks
        assert line.id not in _matched_line_ids(seed_user)


class TestKeptRowsCountsWhatSURVIVES:
    """A figure on a destructive control may not name rows the press destroys.

    Both 2026-08-25 reviews measured the same defect independently: the first
    build counted every creation of every withdrawn act, so a minted envelope
    and its purchase were reported as *"2 rows that match created stay in your
    books"* while the press destroyed both.
    """

    @staticmethod
    def _recorded_into_a_new_envelope(seed_user, amount="-25.00"):
        """Record one bank line as a purchase in an envelope the door mints."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount=amount,
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        created = statement_match.create_purchase_from_line(
            PurchaseCreation(
                line_id=line.id,
                new_envelope=NewEnvelope(
                    name="Public Library",
                    category_id=seed_user["categories"]["Groceries"].id,
                ),
            ),
            a_scope(seed_user),
            _create.MintedEnvelopes.none_yet(),
            an_answers(seed_user),
            applied_by_rule=False,
        )
        db.session.flush()
        return line, created

    def test_a_created_row_the_press_destroys_is_NOT_reported_as_kept(
        self, app, db, seed_user,
    ):
        """Deleting the envelope takes its purchase too, so nothing stays."""
        _, created = self._recorded_into_a_new_envelope(seed_user)
        envelope = db.session.get(Transaction, created.transaction_id)
        assert db.session.query(StatementMatchCreation).count() == 2, (
            "the door records the purchase AND the container it minted"
        )

        preview = match_withdrawal.pending_for_rows([envelope])

        assert preview.matches == 1
        assert preview.kept_rows == 0, (
            "both creations are in the going set -- the envelope itself and "
            "the purchase its foreign key cascades"
        )

    def test_a_created_row_that_SURVIVES_is_reported_and_STAYS(
        self, app, db, seed_user,
    ):
        """The positive control: the container outlives its purchase.

        Without it the arm above could pass on a ``kept_rows`` hard-wired to
        zero, which is the shape this attribute was corrected out of.

        **What the withdrawal costs, stated rather than discovered.**  The act
        goes, and ``StatementMatch.creations`` is ``delete-orphan``, so the
        container's PROVENANCE record goes with it: nothing afterwards records
        that this envelope was minted by an import.  That is the honest end
        state rather than a leak -- the act it belonged to no longer exists, so
        there is no undo left to reach the container, and ruling **R-GG**
        already calls a surviving container *"an ordinary row the owner deletes
        in one click"*.  Since ``X-gb`` that click exists.
        """
        _, created = self._recorded_into_a_new_envelope(seed_user)
        purchase_id = created.entry_id
        envelope_id = created.transaction_id
        purchase = db.session.get(TransactionEntry, purchase_id)

        preview = match_withdrawal.pending_for_rows([])
        assert preview.kept_rows == 0

        pending = match_withdrawal.pending_for_purchase(purchase)
        assert pending.kept_rows == 1, (
            "the envelope is not in the going set, so it STAYS"
        )

        entry_service.delete_entry(purchase_id, seed_user["user"].id)
        db.session.flush()

        assert db.session.get(Transaction, envelope_id) is not None
        assert db.session.query(StatementMatchCreation).count() == 0, (
            "the act is gone, so both its creation records went with it"
        )


class TestTheInvariantHoldsThroughADoorThatDoesNotCallTheRule:
    """The predicate, not the five call sites, is what makes this true.

    Measured by an adversarial review 2026-08-25: ``hard_delete_template``
    removes a template's non-settled rows in ONE bulk statement, from a shipped
    button, and a matched PURCHASE settles the purchase rather than its parent
    -- so the envelope stays Projected, falls in scope, and the act it leaves
    behind used to go on claiming its bank line forever.
    """

    def test_a_bulk_delete_still_frees_the_line(self, app, db, seed_user):
        """No door called the withdrawal, and the line is unexplained anyway."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=day,
        )
        _submit(seed_user, lines=[line], entries=[purchase])
        assert line.id in _matched_line_ids(seed_user)

        # The template door's own statement, verbatim in shape.
        db.session.query(Transaction).filter(
            Transaction.id == envelope.id,
        ).delete(synchronize_session="fetch")
        db.session.flush()

        assert line.id not in _matched_line_ids(seed_user), (
            "the act names no app row, so its membership is not a claim"
        )
        assert db.session.query(StatementMatch).count() == 1, (
            "the act is still there -- the predicate is a READ, not a writer"
        )


class TestReleasingAnActDoesNotWithdrawTwice:
    """``_remove``'s claim that the shared verb is a no-op on its own path.

    :func:`release_match` deletes and FLUSHES the act before removing the rows
    it created, and a subject belongs to at most one act
    (``uq_statement_match_members_transaction``), so the verb's own withdrawal
    finds nothing.

    **The first version of this class did not reach the code it named**: it
    released an act built through the FORM door, which creates nothing, so
    ``planned_removals`` returned no rows and ``_remove`` was never called at
    all -- a control over a claim it could not fail on (adversarial review,
    2026-08-25).  The act below CREATES a container, so the removal path runs.
    """

    def test_a_release_that_REMOVES_A_ROW_reaches_the_shared_verb(
        self, app, db, seed_user,
    ):
        """The act minted an envelope, so releasing it takes that row back."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-25.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        created = statement_match.create_purchase_from_line(
            PurchaseCreation(
                line_id=line.id,
                new_envelope=NewEnvelope(
                    name="Public Library",
                    category_id=seed_user["categories"]["Groceries"].id,
                ),
            ),
            a_scope(seed_user),
            _create.MintedEnvelopes.none_yet(),
            an_answers(seed_user),
            applied_by_rule=False,
        )
        db.session.flush()

        released = statement_match.release_match(
            created.match_id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert released.removed_rows >= 1, (
            "the purchase it created goes; this is the path _remove runs on"
        )
        assert db.session.get(Transaction, created.transaction_id) is None, (
            "the emptied container it minted goes with it"
        )
        assert db.session.query(StatementMatch).count() == 0
        assert line.id not in _matched_line_ids(seed_user)

    def test_a_release_removes_exactly_its_own_act(
        self, app, db, seed_user,
    ):
        """A second act on the same account is untouched by the first's undo."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        first_line = a_bank_line(
            seed_user, statement, amount="-178.32", posted_on=day,
        )
        second_line = a_bank_line(
            seed_user, statement, amount="-95.00", posted_on=day,
        )
        first = a_transaction(seed_user, name="Geico", amount="178.32")
        second = a_transaction(seed_user, name="Water", amount="95.00")
        accepted = _submit(
            seed_user, lines=[first_line], transactions=[first],
        )
        _submit(seed_user, lines=[second_line], transactions=[second])

        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        matched = _matched_line_ids(seed_user)
        assert first_line.id not in matched
        assert second_line.id in matched
        assert db.session.get(Transaction, first.id) is not None, (
            "a release takes back what the act CREATED, and it created nothing"
        )


class TestARowThatIsHalfOfSomethingElseRefusesTheDelete:
    """Two shapes may not be deleted on their own, and the screen asks first.

    ``deletion_refusal`` returns the sentence rather than raising, because the
    action card renders the delete control exactly when it is ``None`` -- the
    same layering ``entry_service.removal_refusal`` has one table over.
    """

    def test_a_transfer_shadow_is_refused_and_says_where_to_go(
        self, app, db, seed_user,
    ):
        """Both legs move together, so neither leaves alone."""
        xfer = _a_transfer(seed_user)
        shadow = (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer.id)
            .first()
        )

        refusal = transaction_service.deletion_refusal(shadow)

        assert refusal is not None
        assert "transfer" in refusal.lower()
        with pytest.raises(ValidationError, match="transfer"):
            transaction_service.delete_transaction(
                shadow, seed_user["user"].id,
            )

    def test_a_CC_payback_is_refused_and_names_Undo_CC(
        self, app, db, seed_user,
    ):
        """Deleting it alone leaves the spending it repays with nothing paying it.

        Its source stays Credit, and Credit is
        ``excludes_from_balance``: the `$200.00` would leave the books with no
        refusal and no trace, which is the shape ruling **N-252** refuses one
        column over for a payback's FIGURE.
        """
        source = a_transaction(
            seed_user, name="Rogue Equipment", amount="200.00", template=False,
        )
        db.session.flush()
        a_later_period(seed_user)
        credit_workflow.mark_as_credit(source.id, seed_user["user"].id)
        db.session.flush()
        payback = credit_workflow.get_active_payback(source.id)

        refusal = transaction_service.deletion_refusal(payback)

        assert refusal is not None
        assert "Undo CC" in refusal
        with pytest.raises(ValidationError, match="Undo CC"):
            transaction_service.delete_transaction(
                payback, seed_user["user"].id,
            )
        db.session.rollback()

    def test_the_refusal_fires_before_anything_is_written(
        self, app, db, seed_user,
    ):
        """A refused delete leaves the database exactly as it was.

        The withdrawal runs FIRST in the verb, so a refusal ordered after it
        would free a bank line for a delete that never happened.
        """
        statement = an_import(seed_user)
        source = a_transaction(
            seed_user, name="Rogue Equipment", amount="200.00", template=False,
        )
        db.session.flush()
        a_later_period(seed_user)
        credit_workflow.mark_as_credit(source.id, seed_user["user"].id)
        db.session.flush()
        payback = credit_workflow.get_active_payback(source.id)
        line = a_bank_line(seed_user, statement, amount="-200.00")
        _submit(seed_user, lines=[line], transactions=[payback])

        with pytest.raises(ValidationError):
            transaction_service.delete_transaction(
                payback, seed_user["user"].id,
            )

        assert line.id in _matched_line_ids(seed_user), (
            "the refusal must fire before the withdrawal"
        )
        assert source.status_id == ref_cache.status_id(StatusEnum.CREDIT)
