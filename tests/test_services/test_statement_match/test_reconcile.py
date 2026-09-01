"""The RECONCILE page: which tab a line lands on, and what each tab counts.

Plan step ``bank_import:X-gj-1a``; rulings **bank_import:R-HP**, **R-HQ**,
**R-HW** and **R-HX**.

**The subject is the PARTITION, not the prose.**  The sentence a card carries
is :mod:`.test_verbs_and_sentence`'s, which needs no database; what needs one
is *which* card exists, *which* tab holds it and *what* each tab claims to
hold -- because those are facts about a real pass over real rows.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest

# Pylint: ``shekel-private-module-import`` -- a test of a service's
# INTERNALS reaches for them by name, which is the convention this
# package's own test modules already keep (``test_bars``,
# ``test_candidates``, ``test_residual``).  The alternative is widening
# the package's public surface for the tests alone, which is the
# "surface nobody asked for" its ``__init__`` refuses in as many words.
# pylint: disable=shekel-private-module-import
from app.services import statement_match
from app.services.statement_match import Tab, reconcile_page
from app.services.statement_match._accepted_view import (
    REGISTER_LIMIT,
    accepted_counts,
    accepted_register,
)
from app.services.statement_match._verbs import Verb
from app.services.bank_agreement import AgreementDay, BankAgreement, ComparedSpan
from app.services.statement_match._create import (
    MintedEnvelopes,
)
from app.services.statement_match._creations import PurchaseCreation
from app.services.statement_match._release import acts_of
from app.services.statement_match._scope import no_period_refusal

from .test_reads_lineless import _planted_lineless

from ._builders import (
    a_bank_line,
    a_purchase,
    a_rule,
    a_scope,
    a_submission,
    a_transaction,
    an_answers,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    filed_acts,
    filed_by,
)

#: What SECU files a card payment under, which ruling **R-GJ** reads: a
#: merchant a source files as paying an account the owner holds.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _page(seed_user, tab, agreement=None, limit=REGISTER_LIMIT):
    """Return the Reconcile page for one tab of the seeded account.

    Args:
        seed_user: The seeded user bundle.
        tab: Which :class:`~app.services.statement_match.Tab`.
        agreement: The bank agreement, or ``None`` -- which is the state of an
            account no import has anchored, and the one most cases are in.
        limit: How many SETTLED acts a settled tab may render, or ``None`` for
            the whole record.  **It defaults to the SHIPPED bound** rather than
            to ``None``, so a case that says nothing about it grades the page
            the route actually renders.

    Returns:
        The page.
    """
    return reconcile_page(a_scope(seed_user), agreement, tab, limit)


def _cards(page):
    """Return every card on a page, across its sections.

    Args:
        page: The page.

    Returns:
        The cards, in section order.
    """
    return [card for section in page.sections for card in section.cards]


def _counts(page):
    """Return the tab bar as a plain dict.

    Args:
        page: The page.

    Returns:
        A dict of :class:`~app.services.statement_match.Tab` to count.
    """
    return {count.tab: count.count for count in page.counts}


class TestALineWithNoAvailableActNeverEntersTheInbox:
    """Ruling **R-HQ**, and the arithmetic that makes the inbox reachable.

    Measured 2026-08-29 on the developer's own account: 9 of his 27
    unexplained lines are card payments no screen in the app can resolve, and
    a queue that cannot empty is not a queue.
    """

    def test_a_card_payment_is_a_TRANSFER_and_not_inbox_work(
        self, app, db, seed_user,
    ):
        """It leaves To explain entirely, and lands where its state belongs."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        counts = _counts(_page(seed_user, Tab.TO_EXPLAIN))

        assert counts[Tab.TO_EXPLAIN] == 0
        assert counts[Tab.TRANSFERS] == 1
        assert _cards(_page(seed_user, Tab.TO_EXPLAIN)) == []

    def test_the_transfer_card_offers_NO_button(self, app, db, seed_user):
        """Ruling **R-HW**: a verb with no door renders no submitting control.

        The sentence opens on TRANSFER, so a card that decided this from the
        sentence alone would render a working-looking OK on every one of the
        developer's nine parked lines.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.TRANSFERS))[0]

        assert card.suggested is Verb.TRANSFER
        assert card.offers_ok is False
        assert card.sweep_class is None

    def test_the_line_still_carries_the_BAR_s_own_sentence(
        self, app, db, seed_user,
    ):
        """Ruling **R-GJ**'s bar is unchanged; only where it renders moved."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.TRANSFERS))[0]

        assert card.panel.notes
        assert any("Capital One" in note for note in card.panel.notes)


class TestAStandingNeverAnswerIsAlreadyASkip:
    """Ruling **R-HP**: a line deliberately explained by nothing is SKIPPED.

    **This arm has never rendered on the developer's data and is built
    anyway**, because the predicate is real and the data is one account's:
    measured 2026-08-29, all 9 of his parked lines carry BOTH bars, so every
    one is a transfer.  A merchant he answers *never a purchase* for that no
    source files as an account payment is the case with only the first.

    **It needs none of** ``X-gj-4``'s **store**: the standing answer IS the
    disposition, so the Skipped tab has members before a disposition column
    exists.
    """

    def _a_never_answered_swipe(self, seed_user, db):
        """Stage one outflow whose merchant the owner has answered NEVER for.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.

        Returns:
            Nothing; the rows are staged and committed.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Foundation Donation", amount="-4.00",
        )
        a_rule(seed_user, "Foundation Donation")
        db.session.commit()

    def test_it_lands_on_SKIPPED_and_not_on_transfers(
        self, app, db, seed_user,
    ):
        """The two bars are different kinds of fact and may not be collapsed.

        Filing the owner's own decision under Transfers would tell them their
        bank had decided for them -- which is the collapse
        :class:`~app.services.statement_match._bars.CreationBar` exists to
        refuse.
        """
        self._a_never_answered_swipe(seed_user, db)

        counts = _counts(_page(seed_user, Tab.TO_EXPLAIN))

        assert counts[Tab.SKIPPED] == 1
        assert counts[Tab.TRANSFERS] == 0
        assert counts[Tab.TO_EXPLAIN] == 0

    def test_its_sentence_is_PAST_tense_and_names_the_answer(
        self, app, db, seed_user,
    ):
        """The decision has already been made, so the card reports it."""
        self._a_never_answered_swipe(seed_user, db)

        card = _cards(_page(seed_user, Tab.SKIPPED))[0]
        said = " ".join(span.text or "" for span in card.sentence)

        assert card.suggested is Verb.SKIP
        assert said.startswith(Verb.SKIP.past)
        assert "is never a purchase" in said
        assert card.offers_ok is False


class TestAnUNANSWEREDInflowIsNeverPreFilled:
    """Ruling **bank_import:R-HX**, which bounds **R-HS**.

    Recording a deposit as uncategorized INCOME is one act, and being the ONLY
    act is not a justification: a merchant credit is a refund, and filing one
    as income is the wrong act.  On the developer's own account 16 of the 18
    inbox lines are deposits.

    **What R-HX bounded was an app that could not DEFEND a destination**, and
    plan step ``bank_import:X-gj-2a`` shipped the one it named -- so this class
    now grades the half that is unchanged: a deposit no standing rule answers
    for still asks.  Its sibling
    :class:`TestAnANSWEREDInflowIsPreFilledByItsRule` grades the other half.
    """

    def test_a_deposit_asks_rather_than_proposing(self, app, db, seed_user):
        """Its card reads *Choose*, so nothing is one click from landing."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Dividend Earned", amount="0.15",
        )
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert len(cards) == 1
        assert cards[0].suggested is None
        assert cards[0].offers_ok is False
        assert cards[0].sentence[0].text == "Choose"

    def test_it_is_still_ADDable_from_the_panel(self, app, db, seed_user):
        """Asking is not refusing: the door exists and the panel offers it."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Dividend Earned", amount="0.15",
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.TO_EXPLAIN))[0]

        assert card.panel.offer_for(Verb.ADD).is_open


class TestTheTabsPartitionWhatTheAccountHolds:
    """Every tab's count is a claim, and the claims may not overlap."""

    def test_an_empty_tab_renders_no_section_at_all(
        self, app, db, seed_user,
    ):
        """A heading over no rows reads as work the owner has to do."""
        an_envelope(seed_user)
        db.session.commit()

        assert _page(seed_user, Tab.SKIPPED).sections == ()
        assert _page(seed_user, Tab.TRANSFERS).sections == ()

    def test_the_inbox_groups_by_what_SUGGESTED_the_verb(
        self, app, db, seed_user,
    ):
        """Ruling **R-HP** replaced **R-HB**'s visible evidence grouping.

        A swipe no rule reaches has nothing suggesting a verb for it, so it
        sits under *Nothing suggested* rather than under a heading claiming
        the app looked and found something.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN)

        assert [section.section.heading for section in page.sections] == [
            "Nothing suggested",
        ]
        assert page.sections[0].count == 1


class TestASettledTabNeverPromisesMoreThanItRenders:
    """The bound is applied to the tab's OWN half, and it travels with it.

    :func:`~app.services.statement_match._accepted_view.accepted_register`
    bounds at :data:`REGISTER_LIMIT`.  A caller that took that bounded list
    and THEN dropped the other half would render fewer rows than its own tab
    caption claims -- with 60 hand acts and 10 rule acts, about 43 rows under
    a tab reading 60.  Narrowing before the bound is what makes the caption
    true, and carrying ``withheld`` is what keeps the truncation from being
    silent.
    """

    def test_narrowing_happens_BEFORE_the_bound_and_in_SQL(
        self, app, db, seed_user,
    ):
        """Each half is loaded and bounded on its own, and the other is not.

        Asserted over STAGED acts rather than an empty account, where every
        clause here is satisfied by zero: ``all([])`` is True twice and
        ``0 + 0 == 0``.
        """
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Amazon", envelope, by_rule=True)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False)
        db.session.commit()
        owner, account = seed_user["user"].id, seed_user["account"].id

        by_hand = accepted_register(owner, account, applied_by_rule=False)
        by_rule = accepted_register(owner, account, applied_by_rule=True)
        both = accepted_register(owner, account)

        assert len(by_hand.shown) == 1
        assert len(by_rule.shown) == 1
        assert all(not act.applied_by_rule for act in by_hand.shown)
        assert all(act.applied_by_rule for act in by_rule.shown)
        assert len(both.shown) == 2
        # The half nobody asked for is never LOADED, let alone priced.
        assert len(acts_of(owner, account, applied_by_rule=True)) == 1
        assert len(acts_of(owner, account)) == 2

    def test_the_counts_the_tab_bar_reads_are_the_same_partition(
        self, app, db, seed_user,
    ):
        """The FILTER aggregate, over a non-zero set, scoped to one owner."""
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Amazon", envelope, by_rule=True)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False)
        db.session.commit()

        counts = accepted_counts(
            seed_user["user"].id, seed_user["account"].id,
        )

        assert (counts.total, counts.by_rule, counts.by_hand) == (2, 1, 1)

    def test_it_counts_no_other_owner_s_acts(
        self, app, db, seed_user, seed_second_user,
    ):
        """Scoped by owner AND account, which is what the write door narrows by."""
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Amazon", envelope, by_rule=True)
        db.session.commit()

        theirs = accepted_counts(
            seed_second_user["user"].id, seed_user["account"].id,
        )

        assert (theirs.total, theirs.by_rule) == (0, 0)

    def _an_act(self, seed_user, db, merchant, envelope, *, by_rule):
        """Record one bank line as a purchase, by hand or by a rule.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
            merchant: The merchant whose line to record.
            envelope: The budget line to file it into.
            by_rule: Whether a STANDING RULE performed it (**R-GT**), which is
                the only fact that decides which of the two settled tabs holds
                it.

        Returns:
            The created purchase.
        """
        line = an_unexplained_outflow(
            seed_user, merchant=merchant, amount="-12.34",
            sequence=0 if by_rule else 1,
        )
        db.session.commit()
        return statement_match.create_purchase_from_line(
            PurchaseCreation(
                line_id=line.id, transaction_id=envelope.id,
            ),
            a_scope(seed_user),
            MintedEnvelopes.none_yet(),
            an_answers(seed_user),
            applied_by_rule=by_rule,
        )

    def test_each_settled_tab_renders_its_OWN_half_and_counts_it(
        self, app, db, seed_user,
    ):
        """The partition is real on both sides, not merely arithmetic.

        Two acts, one performed by a rule and one ticked by a person: each tab
        must render exactly one, and each tab's count must equal what it
        rendered.  On an account with no acts at all every assertion here is
        satisfied by zero, which is why the acts are staged.
        """
        envelope = an_envelope(seed_user)
        self._an_act(seed_user, db, "Amazon", envelope, by_rule=True)
        self._an_act(seed_user, db, "Walmart", envelope, by_rule=False)
        db.session.commit()

        for tab, expected in (
            (Tab.EXPLAINED, False), (Tab.FILED_BY_RULES, True),
        ):
            page = _page(seed_user, tab)
            cards = _cards(page)
            rendered = sum(section.count for section in page.sections)
            withheld = sum(section.withheld for section in page.sections)

            assert len(cards) == 1, tab
            assert cards[0].act.applied_by_rule is expected, tab
            assert rendered + withheld == _counts(page)[tab], tab
            assert rendered <= REGISTER_LIMIT


class TestASettledActSaysWhichVerbItWas:
    """Ruling **R-HP**: MATCH is *a row the books already hold*, ADD is not.

    **A group match with a difference CREATES something and is still a
    MATCH.**  An unbalanced group mints ruling **R-FN**'s residual row and
    records it as a creation, so *did this act create anything* answers ADD
    for the payroll shape this whole arc exists for -- seven deposits and
    `$18,132.63` on the developer's own account.  Found by adversarial review
    2026-08-29.
    """

    def test_a_payroll_group_with_a_residual_is_MATCHED_not_added(
        self, app, db, seed_user,
    ):
        """It re-dates two rows that already existed; that is a match."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.43",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        db.session.commit()
        scope = a_scope(seed_user)
        statement_match.accept_match(
            a_submission(
                scope, lines=[line], transactions=[salary, allowance],
                residual="0.05",
            ),
            scope,
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.EXPLAINED))[0]
        said = " ".join(span.text or "" for span in card.sentence)

        assert card.act.created_every_row is False
        assert said.startswith(Verb.MATCH.past)
        assert not said.startswith(Verb.ADD.past)

    def test_a_purchase_the_act_created_is_ADDED(self, app, db, seed_user):
        """The mirror: nothing pre-existed, so the act recorded new spending."""
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        db.session.commit()
        statement_match.create_purchase_from_line(
            PurchaseCreation(line_id=line.id, transaction_id=envelope.id),
            a_scope(seed_user),
            MintedEnvelopes.none_yet(),
            an_answers(seed_user),
            applied_by_rule=False,
        )
        db.session.commit()

        card = _cards(_page(seed_user, Tab.EXPLAINED))[0]
        said = " ".join(span.text or "" for span in card.sentence)

        assert card.act.created_every_row is True
        assert said.startswith(Verb.ADD.past)


class TestADoorThatWouldRefuseIsNeverOFFERED:
    """Ruling **R-HW**, on the direction that had no refusal at all.

    ``_one_creatable`` withheld only the PLACEMENT for a line no saved pay
    period covers, and left the line in ``creatable`` -- so a reader taking
    membership of that list as the create door's answer reported ADD as open
    on a line ``ReviewScope.period_holding`` refuses by name.  Found by
    adversarial review 2026-08-29 and reproduced on a swipe MADE before the
    calendar opens and POSTED after it.
    """

    def _a_swipe_made_before_the_calendar_opens(self, seed_user, db):
        """Stage a line the calendar covers on its posting day and not before.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.

        Returns:
            Nothing; the rows are staged and committed.
        """
        an_envelope(seed_user)
        opens = seed_user["bootstrap_period"].start_date
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-12.34",
            posted_on=opens,
            transaction_on=opens - timedelta(days=3),
            description="POINT OF SALE DEBIT (Walmart)", merchant="Walmart",
        )
        db.session.commit()

    def test_add_is_SHUT_and_says_why(self, app, db, seed_user):
        """The door's own sentence, before the press rather than after it."""
        self._a_swipe_made_before_the_calendar_opens(seed_user, db)

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert len(cards) == 1
        offer = cards[0].panel.offer_for(Verb.ADD)
        assert offer.is_open is False
        assert "No pay period covers" in offer.waiting_for

    def test_the_screen_and_the_DOOR_say_the_same_thing(
        self, app, db, seed_user,
    ):
        """One spelling: the withheld sentence IS the refusal's own words.

        A screen and a door with two wordings for one rule is this project's
        own root cause, and this rule had three before the step.
        """
        self._a_swipe_made_before_the_calendar_opens(seed_user, db)
        card = _cards(_page(seed_user, Tab.TO_EXPLAIN))[0]

        withheld = card.panel.offer_for(Verb.ADD).waiting_for
        door = no_period_refusal(card.line.happened_on, "this purchase")

        assert withheld == door


class TestOnlyTheInboxSweeps:
    """Ruling **R-FZ(c)**, and where it does NOT reach."""

    def _a_clean_line_and_a_WITHHELD_one(self, seed_user, db):
        """Stage one sweepable card and one the pass would not file.

        Both in ONE pass, so the assertion runs against a NON-EMPTY swept set
        rather than passing because nothing offered a click at all -- which is
        the guard ``test_queue``'s own version of this keeps, and the reason
        it keeps it.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.

        Returns:
            Nothing; the rows are staged and committed.
        """
        day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        a_purchase(seed_user, envelope, amount="180.00")
        a_bank_line(
            seed_user, an_import(seed_user), amount="-180.00", posted_on=day,
            description="POINT OF SALE DEBIT L340 KROGER", sequence_in_group=9,
        )
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        clean = a_transaction(
            seed_user, name="Fuel", amount="200.00", is_envelope=True,
        )
        an_unexplained_outflow(
            seed_user, merchant="Shell", amount="-41.10", sequence=4,
        )
        a_rule(seed_user, "Shell", template_id=clean.template_id)
        db.session.commit()

    def test_the_inbox_OFFERS_a_sweep_and_it_reaches_a_card(
        self, app, db, seed_user,
    ):
        """The whole control, through the real producer rather than by hand.

        Without this, ``_sweeps`` returning ``()`` unconditionally passes the
        suite: every other sweep assertion in this module is ``== ()``.
        """
        self._a_clean_line_and_a_WITHHELD_one(seed_user, db)

        page = _page(seed_user, Tab.TO_EXPLAIN)

        assert page.sweeps, "a sweep must exist or this grades nothing"
        assert sum(sweep.count for sweep in page.sweeps) >= 1
        assert all(sweep.label for sweep in page.sweeps)

    def test_no_SWEPT_card_carries_a_sentence(self, app, db, seed_user):
        """The coupling, not the property in isolation.

        ``_queue`` kept this by giving sweeps to one evidence group; ruling
        **R-HP** replaced that grouping, so the guard has to be re-pinned
        against the producer that replaced it.
        """
        self._a_clean_line_and_a_WITHHELD_one(seed_user, db)

        page = _page(seed_user, Tab.TO_EXPLAIN)
        cards = _cards(page)
        swept = [card for card in cards if card.sweep_class is not None]
        withheld = [card for card in cards if card.panel.notes]

        assert swept, "a swept card must exist or this grades nothing"
        assert withheld, "a withheld card must exist or this grades nothing"
        assert [card for card in swept if card.panel.notes] == []

    def test_the_sweep_COUNT_is_what_the_control_would_reach(
        self, app, db, seed_user,
    ):
        """A caption may not promise a number the click does not deliver.

        Counted in the service for exactly this reason: the review screen it
        replaces computed these in Jinja with ``selectattr | length``.
        """
        self._a_clean_line_and_a_WITHHELD_one(seed_user, db)

        page = _page(seed_user, Tab.TO_EXPLAIN)
        reachable = {}
        for card in _cards(page):
            if card.sweep_class is not None:
                reachable[card.sweep_class] = (
                    reachable.get(card.sweep_class, 0) + 1
                )

        assert {sweep.css_class: sweep.count for sweep in page.sweeps} == (
            reachable
        )

    def test_a_holding_tab_offers_no_sweep(self, app, db, seed_user):
        """There is no act to sweep a line that has none."""
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        assert _page(seed_user, Tab.TRANSFERS).sweeps == ()

    def test_a_settled_tab_offers_no_sweep(self, app, db, seed_user):
        """An undo destroys rows one at a time, under **R-GY**'s confirm."""
        an_envelope(seed_user)
        db.session.commit()

        assert _page(seed_user, Tab.EXPLAINED).sweeps == ()


class TestTheHeroReportsADayBOTHRecordsCanSpeakFor:
    """Plan step ``bank_import:X-gj-1a``'s reason for a new derivation.

    :attr:`~app.services.bank_agreement.BankAgreement.standing_gap` reads the
    span's LAST day whether or not an anchor reaches it, so a hero built on it
    prints a dash for an account whose statements simply stop before the span
    does -- which is :attr:`unpriced_days`, a state the report already
    measures and expects.
    """

    def _agreement(self, days, account_id=1):
        """Return an agreement over hand-built days.

        Args:
            days: The :class:`~app.services.bank_agreement.AgreementDay`
                values, ascending.
            account_id: Which account it is about.  Stated rather than left at
                the seeded account's id by luck, because
                :func:`~app.services.statement_match.reconcile_page` refuses a
                pass and an agreement that name different accounts.

        Returns:
            The :class:`~app.services.bank_agreement.BankAgreement`.

        **``imports`` is ONE run covering the whole hand-built span** (plan
        step ``balance:X-f3c-3``, which added the field).  It is deliberately
        not empty: an empty list means "no import covers any of these days",
        which is a real state and not the one this class is about -- every case
        here is about the HERO, and a hand-built agreement claiming its own
        days are unimported would be describing a different account than the
        one its ``days`` describe.
        """
        return BankAgreement(
            account_id=account_id,
            span=ComparedSpan(
                first_day=days[0].day, last_day=days[-1].day,
                recorded_from=days[0].day, recorded_through=days[-1].day,
            ),
            records_begin=days[0].day,
            anchor=None,
            days=list(days),
            imports=[(days[0].day, days[-1].day)],
        )

    def _day(self, day, bank, books, in_records=True):
        """Return one compared day.

        Args:
            day: The civil day.
            bank: What the bank's record prices it at, or ``None``.
            books: What the app says.
            in_records: Whether the app's records reach this far.

        Returns:
            The :class:`~app.services.bank_agreement.AgreementDay`.
        """
        return AgreementDay(
            day=day, bank_lines=Decimal("0.00"), recorded=Decimal("0.00"),
            asserted=Decimal("0.00"), app_balance=Decimal(books),
            bank_balance=None if bank is None else Decimal(bank),
            in_records=in_records,
        )

    def test_it_walks_BACK_past_a_day_the_bank_cannot_price(self):
        """The whole reason it is not ``span.last_day``."""
        agreement = self._agreement([
            self._day(date(2026, 8, 20), "2459.60", "2501.31"),
            self._day(date(2026, 8, 21), None, "2501.31"),
        ])

        headline = agreement.headline

        assert agreement.standing_gap is None
        assert headline.day == date(2026, 8, 20)
        assert headline.bank_balance == Decimal("2459.60")
        assert headline.gap == Decimal("41.71")

    def test_a_day_the_app_has_no_RECORDS_for_is_not_a_comparison(self):
        """Finding **N-314**: a zero there means *nothing recorded*."""
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2501.31"),
            self._day(
                date(2026, 8, 20), "2459.60", "0.00", in_records=False,
            ),
        ])

        assert agreement.headline.day == date(2026, 8, 19)

    def test_the_page_refuses_DONE_over_a_comparison_that_stopped_early(
        self, app, db, seed_user,
    ):
        """A balanced comparison three weeks old is not *nothing left to do*.

        The headline walks BACK to the last priceable day, so an account whose
        statements stop before its records do can report ``off by $0.00`` about
        a week that is not this one.
        """
        an_envelope(seed_user)
        db.session.commit()
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2459.60"),
            self._day(date(2026, 8, 20), None, "2459.60"),
        ], account_id=seed_user["account"].id)

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=agreement)

        assert page.hero.off_by == Decimal("0.00")
        assert page.hero.to_explain == 0
        assert page.hero.unpriced_after == 1
        assert page.is_done is False

    def test_a_current_and_balanced_account_IS_done(
        self, app, db, seed_user,
    ):
        """The mirror, so the refusal above is not refusing everything."""
        an_envelope(seed_user)
        db.session.commit()
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2459.60"),
            self._day(date(2026, 8, 20), "2459.60", "2459.60"),
        ], account_id=seed_user["account"].id)

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=agreement)

        assert page.hero.unpriced_after == 0
        assert page.is_done is True

    def test_a_pass_and_an_agreement_for_two_accounts_are_REFUSED(
        self, app, db, seed_user,
    ):
        """Two arguments a caller pairs by hand are two chances to mispair.

        The consequence is one account's hero over another's lines, with no
        figure on the page wrong enough to look wrong.
        """
        an_envelope(seed_user)
        db.session.commit()
        other = self._agreement([
            self._day(date(2026, 8, 20), "1.00", "1.00"),
        ], account_id=seed_user["account"].id + 999)

        with pytest.raises(ValueError) as refused:
            _page(seed_user, Tab.TO_EXPLAIN, agreement=other)

        assert "cannot show one account's lines beside another's" in str(
            refused.value,
        )

    def test_an_account_no_import_prices_has_no_headline_at_all(self):
        """Reporting an unknown as agreement is what this arc exists to stop."""
        agreement = self._agreement([
            self._day(date(2026, 8, 20), None, "2501.31"),
        ])

        assert agreement.headline is None

    def test_the_page_states_all_three_figures_or_none(
        self, app, db, seed_user,
    ):
        """A hero printing two of three invites a subtraction it cannot do."""
        an_envelope(seed_user)
        db.session.commit()

        hero = _page(seed_user, Tab.TO_EXPLAIN, agreement=None).hero

        assert (hero.day, hero.bank, hero.books, hero.off_by) == (
            None, None, None, None,
        )
        assert hero.to_explain == 0

    def test_done_is_never_claimed_for_an_account_nothing_prices(
        self, app, db, seed_user,
    ):
        """An empty inbox is half of *done*, and the other half is unknown."""
        an_envelope(seed_user)
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=None)

        assert page.hero.to_explain == 0
        assert page.is_done is False


class TestACaptionCountsOnlyWhatItsTabCanDraw:
    """Finding **bank_import:N-389**, plan step ``bank_import:X-gj-1c``.

    An act that names no bank line has no day, no amount and no wording, so it
    is not a card and never was.  The two readers disagreed about it:
    :func:`~app.services.statement_match._accepted_view.accepted_counts`
    counted the table, and the fold that builds the cards skipped it in Python
    -- so one such act made the Explained caption one higher than the tab could
    deliver.  Measured on a planted act 2026-08-31 before the fix: caption
    ``1``, rendered ``0``, withheld ``0``.

    :data:`~app.services.statement_match._release.NAMES_A_BANK_LINE` is that
    invariant stated once, in SQL, and both readers narrow on it.

    **The state needs a code defect to reach**, which is why it is planted
    through raw SQL: ``record_match`` refuses an empty side at the one writer,
    ``fk_statement_match_members_line_account`` refuses to remove a line a
    match names, and migration ``e4a7c0f13b92`` deleted the acts that already
    held none.  A case over an account with no such act grades nothing here --
    both numbers are already equal -- so every assertion below stands beside a
    REAL act, which is what makes the equality mean something.
    """

    def _one_real_and_one_lineless(self, seed_user, db):
        """Stage one act a tab can draw, and one it cannot.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
        """
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        db.session.commit()
        filed_by(seed_user, line, envelope, by_rule=False)
        db.session.commit()
        _planted_lineless(db, seed_user)

    def test_the_caption_equals_what_the_tab_delivers(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: this read ``1`` against ``0 + 0`` before the fix."""
        self._one_real_and_one_lineless(seed_user, db)

        page = _page(seed_user, Tab.EXPLAINED)
        rendered = sum(section.count for section in page.sections)
        withheld = sum(section.withheld for section in page.sections)

        assert rendered == 1, "the REAL act must be drawn, or nothing is graded"
        assert _counts(page)[Tab.EXPLAINED] == rendered + withheld

    def test_the_counts_exclude_it_and_the_loader_never_yields_it(
        self, app, db, seed_user,
    ):
        """One clause, asked of both readers, over a set holding both kinds."""
        self._one_real_and_one_lineless(seed_user, db)
        owner, account = seed_user["user"].id, seed_user["account"].id

        counts = accepted_counts(owner, account)

        assert (counts.total, counts.by_rule, counts.by_hand) == (1, 0, 1)
        assert len(acts_of(owner, account)) == 1
        assert len(accepted_register(owner, account).shown) == 1

    def test_reaching_one_is_an_ALARM_and_not_a_silence(
        self, app, db, seed_user, caplog,
    ):
        """Skipping SILENTLY was the original defect (reviews, 2026-08-20).

        Such an act goes on claiming its transactions in ``matched_subjects``,
        so those rows can never be matched again and no release control exists
        to free them.  The alarm moved from the fold to the counts read with
        the clause; what may not happen is that it stops being raised.
        """
        self._one_real_and_one_lineless(seed_user, db)

        with caplog.at_level(logging.ERROR):
            _page(seed_user, Tab.EXPLAINED)

        assert any(
            record.__dict__.get("event") == "statement_match_lineless"
            for record in caplog.records
        )


class TestWhichKindOfCARDATabHoldsIsTheTabsOwnFact:
    """Plan step ``bank_import:X-gj-1c``.

    The body asks :attr:`~app.services.statement_match.Tab
    .holds_settled_acts` once and renders one of two partials, because the two
    card kinds carry disjoint controls -- an Undo is a form and may not nest
    in the Apply form, and a settled tab has nothing to Apply.  A TOTAL table
    rather than a membership test, so a sixth tab is a failure here and not a
    bank-line card rendered over acts.
    """

    def test_every_tab_answers(self, app, db, seed_user):
        """Driven from the enum: a new member with no entry raises."""
        assert {tab: tab.holds_settled_acts for tab in Tab} == {
            Tab.TO_EXPLAIN: False,
            Tab.EXPLAINED: True,
            Tab.FILED_BY_RULES: True,
            Tab.TRANSFERS: False,
            Tab.SKIPPED: False,
        }

    def test_it_agrees_with_the_kind_the_sections_actually_hold(
        self, app, db, seed_user,
    ):
        """The predicate and the cards, asked of a page holding both kinds.

        A table can be right about the enum and wrong about the sections; this
        is what pairs the two, over an account that really has an act and
        really has a line to explain.
        """
        envelope = an_envelope(seed_user)
        settled = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        an_unexplained_outflow(
            seed_user, merchant="Lowe's", amount="-35.72", sequence=1,
        )
        db.session.commit()
        filed_by(seed_user, settled, envelope, by_rule=False)
        db.session.commit()

        for tab in (Tab.TO_EXPLAIN, Tab.EXPLAINED):
            cards = _cards(_page(seed_user, tab))

            assert cards, f"{tab} must hold a card, or this grades nothing"
            for card in cards:
                assert hasattr(card, "act") is tab.holds_settled_acts, tab
                assert hasattr(card, "line") is not tab.holds_settled_acts, tab


class TestTheSettledBoundIsLIFTABLE:
    """Ruling **R-GX**'s bound, and the link the register offered past it.

    Plan step ``bank_import:X-gj-1c`` retires the register as a page
    (**R-HU**), so the tab that replaces it has to reach every act the
    register could: on the developer's own account the bound withholds 171 of
    221, which without a way past it would be 171 acts out of reach rather
    than merely unlisted.  The arithmetic is
    ``test_release.TestTheRegisterBoundsWhatItRenders``'s; this grades that
    the PAGE threads the parameter to the settled tabs and to no others.
    """

    def test_the_bound_cuts_and_says_so_and_None_lifts_it(
        self, app, db, seed_user,
    ):
        """One act past the boundary, both renders, one account.

        The bounded render must WITHHOLD -- an equal pair of counts would be
        satisfied by an account that never reached the bound at all.
        """
        filed_acts(seed_user, REGISTER_LIMIT + 1, by_rule=False)

        bounded = _page(seed_user, Tab.EXPLAINED)
        everything = _page(seed_user, Tab.EXPLAINED, limit=None)

        assert sum(one.count for one in bounded.sections) == REGISTER_LIMIT
        assert sum(one.withheld for one in bounded.sections) == 1
        assert sum(one.count for one in everything.sections) == (
            REGISTER_LIMIT + 1
        )
        assert sum(one.withheld for one in everything.sections) == 0

    def test_the_caption_is_the_whole_record_at_either_bound(
        self, app, db, seed_user,
    ):
        """A tab bar states what the account HOLDS, not what it drew.

        Lifting the bound may not move the caption, and the caption must equal
        rendered plus withheld at both -- which is the same equality
        :class:`TestACaptionCountsOnlyWhatItsTabCanDraw` grades from the other
        side.
        """
        filed_acts(seed_user, REGISTER_LIMIT + 1, by_rule=False)

        for page in (
            _page(seed_user, Tab.EXPLAINED),
            _page(seed_user, Tab.EXPLAINED, limit=None),
        ):
            rendered = sum(one.count for one in page.sections)
            withheld = sum(one.withheld for one in page.sections)

            assert _counts(page)[Tab.EXPLAINED] == REGISTER_LIMIT + 1
            assert rendered + withheld == REGISTER_LIMIT + 1


class TestTheChipsNameOnlyATabThatCanRenderThem:
    """Plan step ``bank_import:X-gj-1c`` deleted the *already explained* chip.

    It carried :attr:`~app.services.statement_match._accepted_view
    .AcceptedCounts.total` and led to the register, which lists every accepted
    act.  Once the two settled TABS exist that total is the union of two of
    them, so the chip would have promised a number neither tab delivers -- the
    caption-over-a-count defect ``_queue._sweeps_for`` exists to refuse.

    What must hold now is the property the route rests on: every chip names a
    tab whose count it equals, or names no tab at all.
    """

    def test_no_chip_counts_settled_acts(self, app, db, seed_user):
        """Staged so the deleted chip WOULD have rendered."""
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        db.session.commit()
        filed_by(seed_user, line, envelope, by_rule=False)
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN)

        assert _counts(page)[Tab.EXPLAINED] == 1, (
            "there must BE a settled act, or the deleted chip is absent for "
            "the wrong reason"
        )
        assert not [chip for chip in page.chips if chip.tab is Tab.EXPLAINED]

    def test_every_chip_equals_the_tab_it_names(self, app, db, seed_user):
        """The property the route's URL builder rests on, over real chips.

        A parked card payment gives the Transfers chip a member, so this is
        asked of a chip that exists rather than of an empty tuple.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-793.23",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEBIT CAPITAL ONE MOBILE PMT",
            merchant="Capital One Credit Card",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN)
        counts = _counts(page)

        assert [chip.tab for chip in page.chips] == [Tab.TRANSFERS]
        for chip in page.chips:
            if chip.tab is not None:
                assert chip.count == counts[chip.tab], chip.label


class TestAnANSWEREDInflowIsPreFilledByItsRule:
    """Ruling **R-HT(a)** meeting **R-HS**, plan step ``bank_import:X-gj-2a``.

    **R-HX set a condition rather than a permanent bar**: every unmatched
    inflow reads *Choose what this is* "until ``X-gj-2`` ships R-HT(a)'s inflow
    rule and the destination becomes one the app can defend".  A stated rule is
    exactly that defence, so a deposit it answers for gets the pre-filled
    sentence R-HS asks for -- and one it does not still asks.
    """

    def _deposit(self, seed_user, merchant="Dividend Earned"):
        """Stage one unexplained deposit from *merchant*."""
        an_unexplained_outflow(
            seed_user, merchant=merchant, amount="0.15",
        )

    def test_a_ruled_deposit_states_what_it_will_be_recorded_as(
        self, app, db, seed_user,
    ):
        """*Add as Income: Salary*, with the verb first (**R-HR**)."""
        an_envelope(seed_user)
        category = seed_user["categories"]["Salary"]
        a_rule(
            seed_user, "Dividend Earned", income_category_id=category.id,
        )
        self._deposit(seed_user)
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert len(cards) == 1
        card = cards[0]
        said = " ".join(span.text or "" for span in card.sentence)
        assert card.sentence[0].text == "Add"
        assert category.display_name in said
        assert card.offers_ok is True

    def test_a_merchant_CREDIT_is_PRE_FILLED_as_a_REFUND(
        self, app, db, seed_user,
    ):
        """A refund is not income, and the card offers it as the purchase it is.

        The merchant has a SPENDING answer, so ruling **R-HT(a)** makes this
        credit its INVERSE -- a negative purchase back into the same container.
        **This case asserted the card ASKING until plan step
        ``bank_import:X-gj-2b-2``**, because the act did not exist; the card
        said *Choose* and the panel explained why nothing was filed.

        It now takes the PURCHASE verb, which is the visible half of the
        routing this step corrected: what a line becomes is decided by the
        owner's answer and not by the line's sign, so a claimed credit reaches
        the same card an outflow from that merchant reaches.
        """
        envelope = an_envelope(seed_user)
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        self._deposit(seed_user, merchant="Amazon")
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert len(cards) == 1
        card = cards[0]
        # The PURCHASE verb, not the deposit's *Choose what this is*.
        assert card.sentence[0].text == "Add"
        # And the rule names WHERE, so the owner is not asked to pick.
        assert card.suggested is not None

    def test_an_ALWAYS_ASK_deposit_is_not_pre_filled(
        self, app, db, seed_user,
    ):
        """The answer whose whole content is *keep asking me*.

        It names no income category, so it must reach the card exactly as
        having said nothing does -- and it must NOT be reported as a rule this
        pass withheld, which would be a sentence about a decision the owner
        made saying the app failed to act on it.
        """
        an_envelope(seed_user)
        a_rule(seed_user, "Dividend Earned", always_ask=True)
        self._deposit(seed_user)
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert cards[0].sentence[0].text == "Choose"
        assert not any("refund" in note for note in cards[0].panel.notes)
