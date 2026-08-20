"""The MERCHANT POLICY: where the owner says a merchant's spending goes.

Plan step **bank_import:X-f6a-3d**.  **It MOVES NO MONEY and the first class
below is what pins that**, because it is the property the whole design rests
on: a policy is read to SUGGEST a destination, and the only thing that records
a purchase is an explicit destination submitted for one specific line.  Ruling
**R-FZ**'s *the destination select IS the tick* survives only while that holds.

Measured on the developer's own 2026-08-16 statement against a 2026-08-18
production clone: the 91 unexplained outflows are **21 merchants**, and six
stated answers place **48 of the 91** -- so the sweep those placements feed
turns 48 decisions into one press.  Two of those answers are the ones a
per-line screen could never express: Capital One Credit Card is *never a
purchase* (9 lines, `-$7,412.94` the app already holds as payback rows), and
``Amazon -> Groceries`` reaches ten different pay periods through one template.

Every refusal below is a FIRING CONTROL, written to fail if the refusal were
deleted.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.models.merchant_destination import MerchantDestination
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services.statement_match import (
    MerchantPolicy,
    Placement,
    PlacementKind,
    PolicyAnswer,
    PolicyStatement,
    PolicyView,
    PurchaseDestination,
    statable_merchants,
    review_set,
    state_policies,
)
from app.services.statement_match._placement import (  # pylint: disable=protected-access
    placements_for,
)
from app.services.statement_match._policy import (  # pylint: disable=protected-access
    _refuse_unknown_merchants,
    active_category_ids,
    offerable_templates,
    policies_for,
)

from ._builders import (
    a_bank_line,
    a_later_period,
    a_policy,
    a_scope,
    a_transaction,
    an_import,
)

# Pylint: protected-access -- the three readers above are this PACKAGE's
# internals and have no importer outside it, so exporting them from
# ``statement_match.__init__`` would be the surface rule 13 forbids; the tests
# for a module reach into it, which is the same allowance every sibling here
# takes for ``_candidates`` and ``_propose``.
# pylint: disable=protected-access


def _view(*policies, templates=None, categories=frozenset(), stale=None):
    """Return the policy view these resolvers read, built by hand.

    Built here rather than through :meth:`PolicyView.build` because these cases
    grade the RESOLVER: stating the three inputs literally is what lets a case
    pin one of them (an archived category, a template with no row) without
    arranging the database into that shape first.  The reads themselves are
    graded by the cases that go through ``review_set``.
    """
    return PolicyView(
        policies={policy.merchant: policy for policy in policies},
        template_names=templates or {},
        active_categories=categories,
        stale_templates=stale or {},
    )


def _destination(txn, *, is_settled=False):
    """Return *txn* as the offer value a placement resolves against."""
    return PurchaseDestination(
        transaction_id=txn.id,
        name=txn.name,
        category_id=txn.category_id,
        period_start=txn.pay_period.start_date,
        period_end=txn.pay_period.end_date,
        pay_period_id=txn.pay_period_id,
        is_settled=is_settled,
        template_id=txn.template_id,
    )


class TestStatingAPolicyMovesNoMoney:
    """The property the whole design rests on, asserted rather than assumed."""

    def test_a_stated_policy_records_no_purchase_and_no_row(
        self, app, db, seed_user,
    ):
        """Answering for every merchant writes policies and nothing else.

        THE central claim.  If a policy could record a purchase, then a
        remembered answer would be a default that moves money -- which is
        exactly what ruling R-FZ removed from the destination select, and the
        reason this is a separate door rather than a third kind of batch item.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()
        before_entries = db.session.query(TransactionEntry).count()
        before_txns = db.session.query(Transaction).count()

        state_policies(
            (PolicyStatement(
                "Amazon", PolicyAnswer.TEMPLATE,
                template_id=envelope.template_id,
            ),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert db.session.query(TransactionEntry).count() == before_entries
        assert db.session.query(Transaction).count() == before_txns
        assert db.session.query(MerchantDestination).count() == 1

    def test_a_placement_does_not_TICK_the_line_it_places(
        self, app, db, seed_user,
    ):
        """The select still opens on "leave this line alone".

        A placement is rendered BESIDE the control, never into it.  The screen
        proves the rendered default separately
        (``test_statement_matches.TestTheCreateArm``); what this pins is the
        service half: a placed line is still an offer, and nothing about it
        says the owner accepted anything.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_policy(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        placed = [
            item for item in review.creatable
            if item.placement is not None
        ]
        assert len(placed) == 1
        assert placed[0].placement.kind is PlacementKind.RECORD_IN
        # ...and the account still holds no purchase at all.
        assert db.session.query(TransactionEntry).count() == 0


class TestWhatAPolicyResolvesTo:
    """The four answers a policy comes to for one line, and their reasons."""

    def test_a_TEMPLATE_answer_finds_that_periods_own_row(
        self, app, db, seed_user,
    ):
        """The whole point of keying on a template rather than a row.

        An envelope belongs to ONE pay period -- the 24 unexplained Amazon
        lines on the developer's own statement fall in ten of them -- so the
        answer has to resolve per period, and the template is what does it.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        policy = MerchantPolicy(
            merchant="Amazon", answer=PolicyAnswer.TEMPLATE,
            template_id=envelope.template_id,
        )

        placement = placements_for(
            "Amazon",
            _view(policy, templates={envelope.template_id: "Groceries"}),
            [_destination(envelope)],
        )

        assert placement.kind is PlacementKind.RECORD_IN
        assert placement.destination.transaction_id == envelope.id
        assert placement.select_value == str(envelope.id)

    def test_a_TEMPLATE_answer_with_NO_row_here_says_so(
        self, app, db, seed_user,
    ):
        """Reported, never substituted for.

        Measured on the developer's own clone: template 5 (``Gas``) is
        offerable in 9 of the 11 pay periods their creatable lines fall in, and
        template 38 (``Groceries``) in 10 -- so this is the ordinary state of a
        real statement rather than an edge.  The tempting substitution, falling
        back to a new envelope, would file money somewhere the owner never
        named.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        policy = MerchantPolicy(
            merchant="Amazon", answer=PolicyAnswer.TEMPLATE,
            template_id=envelope.template_id,
        )

        placement = placements_for(
            "Amazon",
            _view(policy, templates={envelope.template_id: "Groceries"}), [],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "Groceries" in placement.unresolved_reason
        assert placement.select_value is None

    def test_a_TEMPLATE_answer_with_TWO_rows_here_refuses_to_guess(
        self, app, db, seed_user,
    ):
        """A template does NOT always make exactly one row in a period.

        Measured on a 2026-08-18 production clone: template 22 (``Kayla's
        Spending Money``) generated two rows in pay period 3, ids 2388 and
        2389.  Picking either would file money in a row the owner did not pick,
        so the placement says which pay period holds two and stops.
        """
        first = a_transaction(seed_user, name="Groceries", is_envelope=True)
        second = a_transaction(
            seed_user, name="Groceries again", is_envelope=True,
        )
        # Two rows from ONE template in ONE period, which is what production
        # holds: ``idx_transactions_template_period_scenario`` is PARTIAL on
        # ``is_override = FALSE``, so an override row sits beside the generated
        # one.  Measured on a 2026-08-18 clone: transactions 2388 (override)
        # and 2389 (generated), both template 22 in pay period 3.
        second.template_id = first.template_id
        second.is_override = True
        db.session.flush()
        policy = MerchantPolicy(
            merchant="Amazon", answer=PolicyAnswer.TEMPLATE,
            template_id=first.template_id,
        )

        placement = placements_for(
            "Amazon", _view(policy, templates={first.template_id: "Groceries"}),
            [_destination(first), _destination(second)],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "2 of them" in placement.unresolved_reason

    def test_a_NEW_ENVELOPE_answer_carries_its_name_and_category(
        self, app, db, seed_user,
    ):
        """The arm that always resolves, because it depends on no existing row.

        On the developer's own data it is the only arm open to the 10 lines in
        a pay period whose every envelope closed at a fixed figure.
        """
        category = seed_user["categories"]["Groceries"]
        policy = MerchantPolicy(
            merchant="Lowe's", answer=PolicyAnswer.NEW_ENVELOPE,
            envelope_name="Lowe's", category_id=category.id,
        )

        placement = placements_for(
            "Lowe's", _view(policy, categories=frozenset({category.id})), [],
        )

        assert placement.kind is PlacementKind.CREATE_NEW
        assert placement.new_envelope.name == "Lowe's"
        assert placement.new_envelope.category_id == category.id
        assert placement.select_value == "new"

    def test_a_NEW_ENVELOPE_answer_whose_CATEGORY_was_archived_says_so(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the active-category clause.

        ``_create._owned_category`` refuses an archived category, because the
        picker renders only active ones -- so a placement naming one would tick
        a line whose submission can never succeed, which is the shape finding
        **N-325** was closed for one field over and which this arc has now
        shipped four times.  Delete the clause and the placement comes back
        ``CREATE_NEW``, the sweep ticks it, and the item is refused a tier
        deeper with a sentence about the category not being the owner's.
        """
        category = seed_user["categories"]["Groceries"]
        policy = MerchantPolicy(
            merchant="Lowe's", answer=PolicyAnswer.NEW_ENVELOPE,
            envelope_name="Lowe's", category_id=category.id,
        )

        placement = placements_for(
            "Lowe's", _view(policy, categories=frozenset()), [],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "archived" in placement.unresolved_reason
        assert placement.select_value is None

    def test_the_active_category_set_is_the_pickers_own(
        self, app, db, seed_user,
    ):
        """The premise for the clause above, read from the database.

        The set is not an assumption about which ids are active: it is the
        same predicate ``category_service.list_active_categories`` renders by.
        """
        archived = seed_user["categories"]["Groceries"]
        archived.is_active = False
        db.session.flush()

        active = active_category_ids(seed_user["user"].id)

        assert archived.id not in active
        assert seed_user["categories"]["Rent"].id in active

    def test_a_NEVER_answer_places_nothing_and_ticks_nothing(
        self, app, db, seed_user,
    ):
        """The answer a derivation from history could never have expressed.

        Capital One Credit Card is 9 of the developer's 91 unexplained
        outflows and `-$7,412.94` of the `-$11,336.36` in that list, and every
        one of them must never become a purchase -- the app holds that money as
        CC Payback rows already.  ``select_value`` is ``None``, so the sweep
        passes over it: saying "never" can never tick anything.
        """
        policy = MerchantPolicy(
            merchant="Capital One Credit Card", answer=PolicyAnswer.NEVER,
        )

        placement = placements_for(
            "Capital One Credit Card", _view(policy), [],
        )

        assert placement.kind is PlacementKind.NOT_A_PURCHASE
        assert placement.select_value is None

    def test_NOT_SAID_and_NEVER_are_different_answers(
        self, app, db, seed_user,
    ):
        """The distinction the whole table exists to hold.

        Without a stored NEVER the screen cannot tell "I have not decided about
        Capital One" from "I have decided: no", so it re-asks nine questions on
        every pass forever.  ``None`` here and a ``NOT_A_PURCHASE`` placement
        are the two answers, and they are not equal.
        """
        assert placements_for("Amazon", _view(), []) is None

        policy = MerchantPolicy(merchant="Amazon", answer=PolicyAnswer.NEVER)
        assert placements_for("Amazon", _view(policy), []) == (
            Placement(
                merchant="Amazon", kind=PlacementKind.NOT_A_PURCHASE,
            )
        )

    def test_a_line_naming_NO_merchant_reaches_no_policy_at_all(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the nullable merchant column.

        A source that names no merchant records ``NULL``, and this is what that
        NULL buys: no policy can key on it.  Delete the ``merchant is None``
        arm and a second adapter's truncated descriptions -- SECU's own OFX
        cuts 326 of 361 to the same 32 characters -- would each key one policy
        and fire it on every merchant behind them.
        """
        policy = MerchantPolicy(merchant="Amazon", answer=PolicyAnswer.NEVER)

        assert placements_for(None, _view(policy), []) is None


class TestStatingAndRestatingAPolicy:
    """The write door: record, restate, withdraw, and say only what changed."""

    def _state(self, db, seed_user, *statements):
        """Run the door for this owner's checking account.

        **It records a bank line for every merchant first**, because that is
        the production precondition: ``state_policies`` refuses a statement
        about a merchant this account has never seen, and it reads that scope
        from the same query it writes through.  A helper that skipped it would
        be exercising a state the app cannot reach.
        """
        statement_import = an_import(seed_user)
        for index, submitted in enumerate(statements):
            a_bank_line(
                seed_user, statement_import, amount="-9.99",
                merchant=submitted.merchant, sequence_in_group=index,
            )
        db.session.flush()
        return state_policies(
            tuple(statements),
            seed_user["user"].id, seed_user["account"].id,
        )

    def test_it_records_each_of_the_three_answers(
        self, app, db, seed_user,
    ):
        """One row per merchant, carrying the columns its answer sets."""
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        category = seed_user["categories"]["Groceries"]

        recorded = self._state(
            db, seed_user,
            PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                            template_id=envelope.template_id),
            PolicyStatement("Lowe's", PolicyAnswer.NEW_ENVELOPE,
                            envelope_name="Lowe's", category_id=category.id),
            PolicyStatement("Capital One Credit Card", PolicyAnswer.NEVER),
        )
        db.session.flush()

        assert len(recorded.stated) == 3
        assert recorded.refused == ()
        held = policies_for(
            seed_user["user"].id, seed_user["account"].id,
        )
        assert held["Amazon"].answer is PolicyAnswer.TEMPLATE
        assert held["Amazon"].template_id == envelope.template_id
        assert held["Lowe's"].answer is PolicyAnswer.NEW_ENVELOPE
        assert held["Lowe's"].envelope_name == "Lowe's"
        assert held["Capital One Credit Card"].answer is PolicyAnswer.NEVER

    def test_restating_the_SAME_answer_writes_nothing_and_says_so(
        self, app, db, seed_user,
    ):
        """The section submits every merchant it renders, so most is no-ops.

        The receipt has to carry the denominator: "0 recorded" with nothing
        beside it would read as though every answer had failed, when in fact
        every one was already what the owner had said.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_policy(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                            template_id=envelope.template_id),
        )

        assert recorded.stated == ()
        assert recorded.unchanged_count == 1

    def test_a_DIFFERENT_answer_replaces_the_row_rather_than_adding_one(
        self, app, db, seed_user,
    ):
        """One answer per merchant is structural, so restating is an UPDATE.

        Two rows would be two answers to one question, which is what
        ``uq_merchant_destinations_owner_account_merchant`` makes unwritable.
        """
        first = a_transaction(seed_user, name="Groceries", is_envelope=True)
        second = a_transaction(seed_user, name="Gas", is_envelope=True)
        a_policy(seed_user, "Amazon", template_id=first.template_id)
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                            template_id=second.template_id),
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        rows = db.session.query(MerchantDestination).all()
        assert len(rows) == 1
        assert rows[0].template_id == second.template_id

    def test_answering_NOT_SAID_WITHDRAWS_a_policy(
        self, app, db, seed_user,
    ):
        """The control's do-nothing option has to be able to mean forget it.

        A policy is a statement about today's budget rather than a judgement --
        when the credit-card arc gives Capital One its own account, the
        Checking-side answer stops being right.  Without this arm an answer
        could be restated but never taken back.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_policy(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.flush()

        recorded = self._state(
            db, seed_user, PolicyStatement("Amazon", answer=None),
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        assert db.session.query(MerchantDestination).count() == 0

    def test_withdrawing_a_policy_that_was_never_stated_changes_nothing(
        self, app, db, seed_user,
    ):
        """Every untouched merchant submits this, so it must be a no-op.

        Twenty-one rows submit on every pass and most carry "I have not said".
        Counting each as a change would make the receipt useless.
        """
        recorded = self._state(
            db, seed_user, PolicyStatement("Amazon", answer=None),
        )

        assert recorded.stated == ()
        # NOT counted as "already answered for": it was never answered for, and
        # the receipt's sentence about the unchanged ones would be false of it.
        assert recorded.unchanged_count == 0
        assert db.session.query(MerchantDestination).count() == 0


class TestWhatTheWriteDoorRefuses:
    """Each refusal, written to fail if the refusal were deleted."""

    def _state(self, db, seed_user, *statements):
        """Run the door for this owner's checking account.

        **It records a bank line for every merchant first**, because that is
        the production precondition: ``state_policies`` refuses a statement
        about a merchant this account has never seen, and it reads that scope
        from the same query it writes through.  A helper that skipped it would
        be exercising a state the app cannot reach.
        """
        statement_import = an_import(seed_user)
        for index, submitted in enumerate(statements):
            a_bank_line(
                seed_user, statement_import, amount="-9.99",
                merchant=submitted.merchant, sequence_in_group=index,
            )
        db.session.flush()
        return state_policies(
            tuple(statements),
            seed_user["user"].id, seed_user["account"].id,
        )

    def test_a_template_on_ANOTHER_ACCOUNT_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """A statement is one bank's record of ONE account.

        ``fk_merchant_destinations_template_account`` makes it unwritable
        anyway, and this is what turns that into a sentence rather than an
        ``IntegrityError`` reaching the owner as "Something went wrong".
        """
        other = a_transaction(
            seed_second_user, name="Groceries", is_envelope=True,
        )
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                            template_id=other.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1
        assert "no recurring envelope on this account" in recorded.refused[0]
        assert db.session.query(MerchantDestination).count() == 0

    def test_a_template_that_does_NOT_TRACK_PURCHASES_is_refused(
        self, app, db, seed_user,
    ):
        """The create door's own refusal, applied where the answer is stated.

        ``entry_service.create_entry`` refuses a parent that does not track
        purchases, so a policy naming a plain budget line would be an answer
        every one of whose placements is refused -- the chooser-that-always-
        fails shape, moved one tier back.
        """
        plain = a_transaction(seed_user, name="Electricity", is_envelope=False)

        recorded = self._state(
            db, seed_user,
            PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                            template_id=plain.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1
        assert db.session.query(MerchantDestination).count() == 0

    def test_an_INCOME_template_is_refused(self, app, db, seed_user):
        """Money coming in is not a purchase.

        ``destinations_for`` excludes an income row for the same reason, so
        offering one here would be the offer-versus-accept drift this arc keeps
        closing.
        """
        income = a_transaction(
            seed_user, name="Paycheck", income=True, is_envelope=True,
        )

        recorded = self._state(
            db, seed_user,
            PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                            template_id=income.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1

    def test_an_INACTIVE_template_is_refused(self, app, db, seed_user):
        """A definition the owner has turned off generates no more rows."""
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                            template_id=envelope.template_id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1

    def test_ANOTHER_OWNERS_category_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The IDOR probe every create door in this project performs.

        A foreign ``category_id`` satisfies a bare foreign key perfectly well.
        ``fk_merchant_destinations_category_owner`` makes it unwritable and
        this makes the refusal a sentence.
        """
        foreign = seed_second_user["categories"]["Groceries"]

        recorded = self._state(
            db, seed_user,
            PolicyStatement("Lowe's", PolicyAnswer.NEW_ENVELOPE,
                            envelope_name="Lowe's", category_id=foreign.id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1
        assert "not one of yours" in recorded.refused[0]
        assert db.session.query(MerchantDestination).count() == 0

    def test_an_ARCHIVED_category_is_refused(self, app, db, seed_user):
        """The picker renders only active categories, so the door takes only those."""
        category = seed_user["categories"]["Groceries"]
        category.is_active = False
        db.session.flush()

        recorded = self._state(
            db, seed_user,
            PolicyStatement("Lowe's", PolicyAnswer.NEW_ENVELOPE,
                            envelope_name="Lowe's", category_id=category.id),
        )

        assert recorded.stated == ()
        assert len(recorded.refused) == 1

    def test_a_REFUSED_statement_costs_only_ITSELF(
        self, app, db, seed_user,
    ):
        """Per-item isolation, and here it protects work rather than money.

        Refusing the whole submission would re-render the section from the
        DATABASE and so discard the other answers the owner had just picked.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        plain = a_transaction(seed_user, name="Electricity", is_envelope=False)

        recorded = self._state(
            db, seed_user,
            PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                            template_id=envelope.template_id),
            PolicyStatement("Walmart", PolicyAnswer.TEMPLATE,
                            template_id=plain.template_id),
            PolicyStatement("Capital One Credit Card", PolicyAnswer.NEVER),
        )
        db.session.flush()

        assert len(recorded.stated) == 2
        assert len(recorded.refused) == 1
        held = policies_for(seed_user["user"].id, seed_user["account"].id)
        assert sorted(held) == ["Amazon", "Capital One Credit Card"]

    def test_a_merchant_this_account_NEVER_SAW_is_refused(
        self, app, db, seed_user,
    ):
        """The scope check, and it is the whole submission's.

        The section renders exactly the merchants this account's recorded lines
        name, so a statement about another cannot have come from this screen --
        there is no pass to salvage.  Without it the table would hold a policy
        for any string a caller liked, which is unbounded write amplification
        against a table with no other ceiling.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()

        with pytest.raises(ValidationError) as caught:
            _refuse_unknown_merchants(
                (PolicyStatement("Nowhere Ltd", PolicyAnswer.NEVER),),
                seed_user["account"].id, {},
            )

        assert "never shown" in str(caught.value)

    def test_a_merchant_this_account_HAS_seen_is_admitted(
        self, app, db, seed_user,
    ):
        """The other side of the scope check, so the refusal is not vacuous."""
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()

        _refuse_unknown_merchants(
            (PolicyStatement("Amazon", PolicyAnswer.NEVER),),
            seed_user["account"].id, {},
        )

    def test_a_merchant_from_a_line_that_is_already_MATCHED_is_admitted(
        self, app, db, seed_user,
    ):
        """The scope is every RECORDED line, not the leftover ones.

        A merchant whose every line is explained today is still one the owner
        may want to answer for -- the next statement brings more of it -- and a
        scope narrowed to the leftovers would refuse an answer the section
        itself renders.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        db.session.flush()

        assert statable_merchants(
            seed_user["account"].id,
        ) == frozenset({"Amazon"})


class TestWhatAPolicyMayNAME:
    """The option list, and that it is the same set the door checks against."""

    def test_it_offers_only_purchase_tracking_expense_definitions(
        self, app, db, seed_user,
    ):
        """One set, read by the control that renders and the door that writes.

        A template this does not return cannot be reached by crafting a
        request, and one it does return cannot be refused by the write door --
        the property ``destinations_for`` rests on, applied one tier up.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_transaction(seed_user, name="Electricity", is_envelope=False)
        a_transaction(
            seed_user, name="Paycheck", income=True, is_envelope=True,
        )

        offered = offerable_templates(seed_user["account"].id)

        assert offered == {envelope.template_id: "Groceries"}

    def test_a_template_on_another_account_is_not_offered(
        self, app, db, seed_user, seed_second_user,
    ):
        """A statement is one bank's record of ONE account."""
        a_transaction(seed_second_user, name="Groceries", is_envelope=True)
        db.session.flush()

        assert offerable_templates(seed_user["account"].id) == {}


class TestTheSectionTheScreenRenders:
    """What the policy control lists, and what it counts."""

    def test_it_lists_every_merchant_with_work_and_every_one_answered_for(
        self, app, db, seed_user,
    ):
        """Both halves, and the second is what makes an answer withdrawable.

        Without it a policy could only be changed while there was still work
        outstanding for that merchant.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="Walmart",
            sequence_in_group=1,
        )
        # Answered for, with nothing pending: its lines are all explained.
        a_policy(seed_user, "Old Merchant", template_id=envelope.template_id)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert [row.merchant for row in review.merchants.merchants] == [
            "Amazon", "Old Merchant", "Walmart",
        ]
        assert review.merchants.answered_count == 1

    def test_a_row_carries_how_many_lines_and_how_much_money_it_decides(
        self, app, db, seed_user,
    ):
        """The section decides several lines at once, so it says how much.

        On the developer's own statement one row of it covers `-$7,412.94`.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="Amazon",
            sequence_in_group=1,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        row = review.merchants.merchants[0]
        assert row.merchant == "Amazon"
        assert row.line_count == 2
        assert row.total == Decimal("-55.00")

    def test_the_sweep_counts_only_what_it_would_TICK(
        self, app, db, seed_user,
    ):
        """The caption cannot promise a number the control does not deliver.

        A "never a purchase" answer places nothing, and so does a policy that
        does not reach this line's pay period -- both have no select value, so
        both are outside the count and outside the sweep.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="Capital One",
            sequence_in_group=1,
        )
        a_policy(seed_user, "Amazon", template_id=envelope.template_id)
        a_policy(seed_user, "Capital One")
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 2
        assert review.placed_by_class == {"into_open": 1}


class TestALineDatedMadeAfterItPosted:
    """Finding **N-325**, ruled 2026-08-19: not offered, and SAID."""

    def test_it_is_not_offered_as_a_purchase(self, app, db, seed_user):
        """The submission could never succeed, so the chooser is not rendered.

        ``entry_service.create_entry`` refuses a purchase whose money left
        before it was spent -- correctly, because money cannot -- so a line the
        bank dates MADE after it POSTED has no day a purchase could happen on.
        0 of the developer's 361 recorded lines are this shape; the OFX
        adapter's own measurement found 2 of 361.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=day,
            transaction_on=day + timedelta(days=1), merchant="Amazon",
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert review.creatable == ()
        assert review.bounds.impossible_day_count == 1
        assert review.bounds.any_limit is True

    def test_a_line_dated_made_BEFORE_it_posted_is_still_offered(
        self, app, db, seed_user,
    ):
        """The firing control's other side, so the exclusion is not vacuous."""
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date + timedelta(days=2)
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=day,
            transaction_on=day - timedelta(days=1), merchant="Amazon",
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 1
        assert review.bounds.impossible_day_count == 0


class TestTheTableRefusesWhatIsNotAnAnswer:
    """The three CHECKs, each shown refusing a row the ORM would happily write."""

    def _row(self, db, seed_user, **columns):
        """Stage a policy row with these columns and flush it."""
        row = MerchantDestination(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant="Amazon",
            **columns,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def test_a_template_AND_a_new_envelope_is_unwritable(
        self, app, db, seed_user,
    ):
        """Two answers to one question.

        ``ck_merchant_destinations_one_answer`` spells the three legal shapes
        as three shapes, because a count-the-NULLs form cannot say this: one
        answer sets two columns and one sets none.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        with pytest.raises(Exception) as caught:
            self._row(
                db, seed_user, template_id=envelope.template_id,
                envelope_name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            )

        assert "ck_merchant_destinations_one_answer" in str(caught.value)

    def test_a_new_envelope_stated_by_HALVES_is_unwritable(
        self, app, db, seed_user,
    ):
        """A name with no category, or a category with no name, is neither answer.

        ``transactions.category_id`` is what every spending report groups by,
        so a row created without one would be invisible to the analysis the
        purchase exists to feed.
        """
        with pytest.raises(Exception) as caught:
            self._row(db, seed_user, envelope_name="Lowe's")

        assert "ck_merchant_destinations_one_answer" in str(caught.value)

    def test_a_BLANK_merchant_is_unwritable(self, app, db, seed_user):
        """A blank key is a policy the owner could neither read nor restate.

        The adapter answers ``None`` for the same input
        (``_secu_csv._stated_merchant``), so the two cannot drift.
        """
        row = MerchantDestination(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant="   ",
        )
        db.session.add(row)
        with pytest.raises(Exception) as caught:
            db.session.flush()

        assert "merchant_not_blank" in str(caught.value)

    def test_TWO_answers_for_ONE_merchant_are_unwritable(
        self, app, db, seed_user,
    ):
        """One answer per merchant per account, structurally."""
        a_policy(seed_user, "Amazon")
        with pytest.raises(Exception) as caught:
            self._row(db, seed_user)

        assert "uq_merchant_destinations_owner_account_merchant" in str(
            caught.value,
        )


class TestAPolicyOutLIVESTheLinesThatPrompted_It:
    """A merchant with an answer stays answerable when its lines are gone."""

    def test_an_ANSWERED_merchant_is_in_scope_with_no_recorded_line(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the second half of the statable set.

        Narrow the scope to the recorded lines and this fails: the section
        renders an answered merchant whichever half it came from, so submitting
        it would then refuse the WHOLE pass -- and a policy would become
        unwithdrawable the moment its lines went.  **No door in `app/` deletes
        an import today**; that is finding **N-302**, owned by `X-f6a-4`, which
        is the next step in this arc.  One step from live, not hypothetical.
        """
        a_policy(seed_user, "Gone Merchant")
        db.session.flush()

        assert "Gone Merchant" in statable_merchants(
            seed_user["account"].id,
        )
        # ...and the door admits it, reading the same policies it will write.
        state_policies(
            (PolicyStatement("Gone Merchant", answer=None),),
            seed_user["user"].id, seed_user["account"].id,
        )

    def test_ANOTHER_OWNERS_policy_does_not_widen_this_owners_scope(
        self, app, db, seed_user, seed_second_user,
    ):
        """The second half is scoped by owner AND account, like the first.

        Without both clauses the set would be "any merchant anyone has answered
        for", which is a scope check that admits a string this owner has never
        seen -- the thing the check exists to refuse.
        """
        a_policy(seed_second_user, "Theirs", account=seed_second_user["account"])
        db.session.flush()

        assert "Theirs" not in statable_merchants(
            seed_user["account"].id,
        )


class TestEveryScopeFilterHasAControlThatFiresOnItsOwn:
    """One guard per case, because a case that needs two to fail grades neither.

    **Every filter below was measured to survive its own deletion** by an
    adversarial test-quality review on 2026-08-19: the suite stayed green with
    each of them removed one at a time, and the one case that looked like a
    control for two of them differed on both, so it fired only when both went.
    A guard nothing can observe is not a guard.
    """

    def test_the_recorded_scope_is_THIS_ACCOUNTS_lines_alone(
        self, app, db, seed_user, seed_second_user,
    ):
        """THE security-relevant one, and it fired on nothing.

        Delete ``BankStatementLine.account_id == account_id`` from
        ``statable_merchants`` and every merchant on every account in the
        database widens this account's statable scope -- which is to say the
        refusal stops refusing, and a caller may write a policy row keyed on
        any merchant any other owner's bank ever showed.
        """
        theirs = an_import(seed_second_user, account=seed_second_user["account"])
        a_bank_line(
            seed_second_user, theirs, amount="-9.99", merchant="Theirs Only",
        )
        db.session.flush()

        assert "Theirs Only" not in statable_merchants(
            seed_user["account"].id,
        )

    def test_a_line_naming_NO_merchant_widens_the_scope_by_nothing(
        self, app, db, seed_user,
    ):
        """``merchant IS NOT NULL`` had no control either.

        Without it the set carries a ``None``, which then compares against
        every submitted string -- and ``sorted()`` over it raises, so the
        refusal path 500s instead of refusing.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant=None)
        db.session.flush()

        assert statable_merchants(seed_user["account"].id) == frozenset()

    def test_policies_are_read_for_THIS_OWNER_and_THIS_ACCOUNT(
        self, app, db, seed_user, seed_second_user,
    ):
        """Both filters on ``policies_for``, each observed on its own.

        The OWNER filter protects against a CALLER, not against a data state:
        ``fk_merchant_destinations_owner`` already holds a row's owner equal to
        its account's, so no row with the wrong pair exists to be found.  What
        it refuses is a producer asked for one owner's answers with another
        owner's id -- which is why the case passes a mismatched pair rather
        than staging an impossible row.  A first version of this test staged
        the other owner's policy on the OTHER owner's account, where the
        account filter alone answers correctly, so it graded nothing.

        The ACCOUNT filter is the one a screen can hit: a Checking answer
        suggested on a card statement resolves to nothing at best, and the
        credit-card arc is about to give this owner a second account.
        """
        mine = a_transaction(seed_user, name="Groceries", is_envelope=True)
        a_policy(seed_user, "Amazon", template_id=mine.template_id)
        theirs = a_transaction(
            seed_second_user, name="Groceries", is_envelope=True,
        )
        a_policy(seed_second_user, "Theirs", template_id=theirs.template_id,
                 account=seed_second_user["account"])
        db.session.flush()

        # Asked for the WRONG owner of a real account: nothing.
        assert policies_for(
            seed_second_user["user"].id, seed_user["account"].id,
        ) == {}
        # Asked for the right owner and the WRONG account: nothing.
        assert policies_for(
            seed_user["user"].id, seed_second_user["account"].id,
        ) == {}
        # ...and the right pair finds exactly its own.
        assert sorted(
            policies_for(seed_user["user"].id, seed_user["account"].id),
        ) == ["Amazon"]


class TestRestatingOneCOLUMNIsStillAChange:
    """`_same_answer` compares three columns, and two graded nothing."""

    def _record(self, db, seed_user, merchant, **columns):
        """Stage a line for *merchant* and a policy row carrying *columns*."""
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant=merchant)
        a_policy(seed_user, merchant, **columns)
        db.session.flush()

    def test_changing_only_the_NAME_is_written(self, app, db, seed_user):
        """Delete the ``envelope_name`` term and this restatement is dropped.

        The owner renames the envelope a merchant should get, the receipt says
        nothing changed, and the next statement still creates it under the old
        name.
        """
        category = seed_user["categories"]["Groceries"]
        self._record(
            db, seed_user, "Lowe's", envelope_name="Lowe's",
            category_id=category.id,
        )

        recorded = state_policies(
            (PolicyStatement("Lowe's", PolicyAnswer.NEW_ENVELOPE,
                             envelope_name="Yard & Garden",
                             category_id=category.id),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        assert policies_for(
            seed_user["user"].id, seed_user["account"].id,
        )["Lowe's"].envelope_name == "Yard & Garden"

    def test_changing_only_the_CATEGORY_is_written(self, app, db, seed_user):
        """Delete the ``category_id`` term and this restatement is dropped.

        The envelope keeps being created under the category the owner moved it
        away from, which is what every spending report groups by.
        """
        was = seed_user["categories"]["Groceries"]
        now = seed_user["categories"]["Rent"]
        self._record(
            db, seed_user, "Lowe's", envelope_name="Lowe's", category_id=was.id,
        )

        recorded = state_policies(
            (PolicyStatement("Lowe's", PolicyAnswer.NEW_ENVELOPE,
                             envelope_name="Lowe's", category_id=now.id),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert len(recorded.stated) == 1
        assert policies_for(
            seed_user["user"].id, seed_user["account"].id,
        )["Lowe's"].category_id == now.id

    def test_a_NEW_answer_is_stored_TRIMMED(self, app, db, seed_user):
        """The write-path trim, which the no-op case above cannot observe.

        That case is decided by the unchanged check, which trims before it
        compares; this is the first statement, where there is nothing to
        compare against.  Stored untrimmed, the name differs from what the
        database CHECK reads (``btrim``) and from what the owner typed next
        time, so every later Save rewrites the row.
        """
        category = seed_user["categories"]["Groceries"]
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Lowe's")
        db.session.flush()

        state_policies(
            (PolicyStatement("Lowe's", PolicyAnswer.NEW_ENVELOPE,
                             envelope_name="  Lowe's  ",
                             category_id=category.id),),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert policies_for(
            seed_user["user"].id, seed_user["account"].id,
        )["Lowe's"].envelope_name == "Lowe's"

    def test_a_name_differing_only_by_WHITESPACE_is_not_a_change(
        self, app, db, seed_user,
    ):
        """`_trimmed` had no control, and the database CHECK compares btrim.

        Without it the section rewrites that row on every Save, and the stored
        name drifts from the one the CHECK would accept.
        """
        category = seed_user["categories"]["Groceries"]
        self._record(
            db, seed_user, "Lowe's", envelope_name="Lowe's",
            category_id=category.id,
        )

        recorded = state_policies(
            (PolicyStatement("Lowe's", PolicyAnswer.NEW_ENVELOPE,
                             envelope_name="  Lowe's  ",
                             category_id=category.id),),
            seed_user["user"].id, seed_user["account"].id,
        )

        assert recorded.stated == ()
        assert recorded.unchanged_count == 1


class TestOneSubmISSIONNamingOneMerchantTwice:
    """The crafted shape whose blast radius is the whole pass."""

    def test_the_second_statement_RESTATES_rather_than_inserting(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for keeping ``stored`` in step.

        Without it both statements take the insert branch,
        ``uq_merchant_destinations_owner_account_merchant`` raises an
        ``IntegrityError`` -- which is not a designed refusal, so it escapes
        the per-item savepoint into the route's database arm and rolls back
        every answer that had already landed.  Unreachable from the rendered
        form, which emits one row per merchant; reachable by a crafted body.
        """
        first = a_transaction(seed_user, name="Groceries", is_envelope=True)
        second = a_transaction(seed_user, name="Gas", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Amazon")
        db.session.flush()

        recorded = state_policies(
            (
                PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                                template_id=first.template_id),
                PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                                template_id=second.template_id),
            ),
            seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert recorded.refused == ()
        rows = db.session.query(MerchantDestination).all()
        assert len(rows) == 1
        # The LAST statement wins, which is what restating means.
        assert rows[0].template_id == second.template_id


class TestAStoredAnswerThatStoppedBeingOfferable:
    """A template deactivated under a policy's feet (finding from review)."""

    def test_the_view_can_still_NAME_it(self, app, db, seed_user):
        """THE FIRING CONTROL for the whole ``stale_templates`` derivation.

        Without it the section has no option carrying the stored value, so the
        select shows and submits its FIRST -- *I have not said* -- and the next
        Save silently WITHDRAWS a policy the owner never touched.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        a_policy(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.flush()

        view = PolicyView.build(
            seed_user["user"].id, seed_user["account"].id,
        )

        assert view.template_names == {}
        assert view.stale_templates == {envelope.template_id: "Groceries"}
        assert view.label_for(envelope.template_id) == "Groceries"

    def test_restating_it_UNCHANGED_is_a_no_op_rather_than_a_refusal(
        self, app, db, seed_user,
    ):
        """The order of the unchanged check is the fix, not a saving.

        The section renders the stale answer back, so Save submits it.
        Validating before comparing would refuse it -- reporting a refusal for
        a merchant the owner never touched, on a press about a different one.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-9.99", merchant="Amazon")
        a_policy(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.flush()

        recorded = state_policies(
            (PolicyStatement("Amazon", PolicyAnswer.TEMPLATE,
                             template_id=envelope.template_id),),
            seed_user["user"].id, seed_user["account"].id,
        )

        assert recorded.refused == ()
        assert recorded.stated == ()
        assert recorded.unchanged_count == 1
        assert db.session.query(MerchantDestination).count() == 1


class TestAMerchantSectionOverMixedLines:
    """The NULL-merchant guard is reachable and was graded by nothing."""

    def test_a_NULL_merchant_line_contributes_no_row(
        self, app, db, seed_user,
    ):
        """Remove the guard and ``sorted()`` raises on a set holding ``None``.

        Both shapes in one account is the ordinary state of a second adapter:
        one source names merchants and the other does not.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant=None,
            sequence_in_group=1,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.creatable) == 2
        assert [row.merchant for row in review.merchants.merchants] == [
            "Amazon",
        ]

    def test_two_spellings_of_one_merchant_are_two_policies(
        self, app, db, seed_user,
    ):
        """The model's own load-bearing claim, which nothing graded.

        ``merchant_destinations`` does not case-fold, on the ground that
        deciding two bank strings name one merchant is a guess.  That is a real
        consequence rather than a detail: the owner answers twice, and the
        screen has to show both rows so they can.
        """
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-25.00", merchant="Amazon")
        a_bank_line(
            seed_user, statement, amount="-30.00", merchant="amazon",
            sequence_in_group=1,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert [row.merchant for row in review.merchants.merchants] == [
            "Amazon", "amazon",
        ]


class TestANewEnvelopeAnswerReusesOneOfThatNameHere:
    """Finding **N-327**, developer ruling 2026-08-20 (plan step X-f6a-4).

    A ``new envelope called X`` answer used to mint unconditionally, so a
    policy fragmented its own budget line: measured on the developer's own
    statement, a ``Lowe's`` answer places 4 lines over 3 pay periods, so ONE
    press made 4 envelopes -- two of them in the same period -- and the next
    statement made more beside them, because an ad-hoc row carries no identity
    across periods for anything to converge on.

    **The suggestion is what changed, never the tick.**  The placement prints
    beside the line's own destination select, which still opens on *leave this
    line alone* (ruling **R-FZ**), so the owner sees the envelope it would
    reuse and may pick another -- including "a new envelope" again.
    """

    def test_an_envelope_of_that_name_HERE_is_recorded_into(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: this is the cross-STATEMENT half of the convergence.

        The envelope a previous statement created is offered to this line, so
        the answer resolves to it instead of minting a second one beside it.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False,
        )
        policy = MerchantPolicy(
            merchant="Lowe's", answer=PolicyAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            "Lowe's", _view(policy, categories={category.id}), [_destination(existing)],
        )

        assert placement.kind is PlacementKind.RECORD_IN
        assert placement.destination.transaction_id == existing.id
        assert placement.select_value == str(existing.id)

    def test_a_period_holding_NONE_of_that_name_still_creates(
        self, app, db, seed_user,
    ):
        """The arm the answer exists for, unchanged.

        Several of the developer's merchants -- a hardware store, a parks fee,
        two subscriptions -- have no envelope in ANY period, which is why
        ruling R-FX made creating one an answer at all.
        """
        category = seed_user["categories"]["Groceries"]
        other = a_transaction(
            seed_user, name="Groceries", is_envelope=True, template=False,
        )
        policy = MerchantPolicy(
            merchant="Lowe's", answer=PolicyAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            "Lowe's", _view(policy, categories={category.id}), [_destination(other)],
        )

        assert placement.kind is PlacementKind.CREATE_NEW
        assert placement.new_envelope.name == "Home Improvement"
        assert placement.select_value == "new"

    def test_TWO_of_that_name_here_is_reported_rather_than_guessed(
        self, app, db, seed_user,
    ):
        """The same rule, and the same sentence shape, a template answer takes.

        Reachable on data the defect itself produced -- two same-named
        envelopes in one period is exactly what one press used to make -- which
        is why it may not be papered over by picking the first.
        """
        category = seed_user["categories"]["Groceries"]
        one = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False,
        )
        two = a_transaction(
            seed_user, name="Home Improvement", amount="1.00",
            is_envelope=True, template=False,
        )
        policy = MerchantPolicy(
            merchant="Lowe's", answer=PolicyAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            "Lowe's", _view(policy, categories={category.id}),
            [_destination(one), _destination(two)],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "already holds 2 of them" in placement.unresolved_reason
        assert placement.select_value is None

    def test_a_same_named_envelope_under_ANOTHER_category_is_NOT_reused(
        self, app, db, seed_user,
    ):
        """MONEY-ADJACENT FIRING CONTROL: a policy names a name AND a category.

        Reusing on the name alone would file this merchant's spending under a
        category the owner did not pick -- which is what every spending report
        groups by, and what the within-press registry already keys on.  A first
        draft of this arm compared the name alone, so the two halves of one
        rule disagreed; found by adversarial design review 2026-08-20.
        """
        answered = seed_user["categories"]["Groceries"]
        other = next(
            category for name, category in seed_user["categories"].items()
            if name != "Groceries"
        )
        existing = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False, category=other,
        )
        policy = MerchantPolicy(
            merchant="Lowe's", answer=PolicyAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=answered.id,
        )

        placement = placements_for(
            "Lowe's",
            _view(policy, categories={answered.id, other.id}),
            [_destination(existing)],
        )

        assert placement.kind is PlacementKind.CREATE_NEW
        assert placement.new_envelope.category_id == answered.id

    def test_a_same_named_TEMPLATE_row_is_NOT_reused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: naming a template is a DIFFERENT answer.

        It has its own resolution beside this one, including the "this pay
        period holds two of them, pick the one you mean" report that a
        recurring definition needs.  An owner who means the recurring envelope
        has that answer available and did not choose it, so converging onto it
        here would make the two answers indistinguishable in effect -- and
        would silently bypass the reporting.
        """
        category = seed_user["categories"]["Groceries"]
        generated = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
        )
        assert generated.template_id is not None
        policy = MerchantPolicy(
            merchant="Lowe's", answer=PolicyAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            "Lowe's", _view(policy, categories={category.id}),
            [_destination(generated)],
        )

        assert placement.kind is PlacementKind.CREATE_NEW

    def test_it_matches_the_NAME_and_not_the_label(
        self, app, db, seed_user,
    ):
        """The label appends the pay-period span for a reader.

        A rule comparing against the label would be comparing against the span
        too, so it would never match -- and the convergence would silently do
        nothing while every test above still passed if they compared labels.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False,
        )
        offered = _destination(existing)
        assert offered.label != offered.name

        placement = placements_for(
            "Lowe's",
            _view(
                MerchantPolicy(
                    merchant="Lowe's", answer=PolicyAnswer.NEW_ENVELOPE,
                    envelope_name="Home Improvement",
                    category_id=category.id,
                ),
                categories={category.id},
            ),
            [offered],
        )

        assert placement.kind is PlacementKind.RECORD_IN

    def test_an_ARCHIVED_category_still_refuses_before_any_of_this(
        self, app, db, seed_user,
    ):
        """The order of the refusals: an unusable answer resolves to nothing.

        Reusing an envelope of that name would look like a repair, and it would
        be applying an answer the owner can no longer restate.
        """
        category = seed_user["categories"]["Groceries"]
        existing = a_transaction(
            seed_user, name="Home Improvement", is_envelope=True,
            template=False,
        )
        policy = MerchantPolicy(
            merchant="Lowe's", answer=PolicyAnswer.NEW_ENVELOPE,
            envelope_name="Home Improvement", category_id=category.id,
        )

        placement = placements_for(
            "Lowe's", _view(policy, categories=frozenset()),
            [_destination(existing)],
        )

        assert placement.kind is PlacementKind.UNRESOLVED
        assert "archived" in placement.unresolved_reason


class TestTheScreenSaysWhichLineCREATESTheEnvelope:
    """Finding **N-327**: one press mints one envelope per answer per period.

    ``placements_for`` resolves ONE line against its own period and cannot know
    what another line in the same pass will do, so the flag that says *an
    earlier line here already creates this* is set by the reader -- the only
    thing that sees more than one line at a time.

    **It is about the SENTENCE, not the act.**  Both lines still carry the same
    select value (``new``), because the write converges; what this buys is that
    the screen says so BEFORE the press rather than the receipt saying it after.
    """

    def test_the_SECOND_line_of_one_answer_says_it_joins(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: without it both lines read as making their own."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        category = seed_user["categories"]["Groceries"]
        for amount in ("-30.00", "-45.00"):
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {amount}", merchant="Lowe's",
            )
        a_policy(
            seed_user, "Lowe's", envelope_name="Home Improvement",
            category_id=category.id,
        )

        review = review_set(a_scope(seed_user))

        joining = [
            line.placement.joins_new for line in review.creatable
            if line.placement is not None and line.placement.creates
        ]
        assert joining == [False, True]

    def test_lines_in_DIFFERENT_periods_each_create_their_own(
        self, app, db, seed_user,
    ):
        """The key carries the period, so neither joins the other."""
        statement = an_import(seed_user)
        category = seed_user["categories"]["Groceries"]
        first_day = seed_user["bootstrap_period"].start_date
        later_day = a_later_period(seed_user).start_date
        for amount, day in (("-30.00", first_day), ("-45.00", later_day)):
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {day}", merchant="Lowe's",
            )
        a_policy(
            seed_user, "Lowe's", envelope_name="Home Improvement",
            category_id=category.id,
        )

        review = review_set(a_scope(seed_user))

        joining = [
            line.placement.joins_new for line in review.creatable
            if line.placement is not None and line.placement.creates
        ]
        assert joining == [False, False]
