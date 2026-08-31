"""The RECONCILE page: which tab a line lands on, and what each tab counts.

Plan step ``bank_import:X-gj-1a``; rulings **bank_import:R-HP**, **R-HQ**,
**R-HW** and **R-HX**.

**The subject is the PARTITION, not the prose.**  The sentence a card carries
is :mod:`.test_verbs_and_sentence`'s, which needs no database; what needs one
is *which* card exists, *which* tab holds it and *what* each tab claims to
hold -- because those are facts about a real pass over real rows.
"""

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

from ._builders import (
    a_bank_line,
    a_purchase,
    a_bars,
    a_rule,
    a_submission,
    a_transaction,
    an_import,
    a_scope,
    an_envelope,
    an_unexplained_outflow,
)

#: What SECU files a card payment under, which ruling **R-GJ** reads: a
#: merchant a source files as paying an account the owner holds.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _page(seed_user, tab, agreement=None):
    """Return the Reconcile page for one tab of the seeded account.

    Args:
        seed_user: The seeded user bundle.
        tab: Which :class:`~app.services.statement_match.Tab`.
        agreement: The bank agreement, or ``None`` -- which is the state of an
            account no import has anchored, and the one most cases are in.

    Returns:
        The page.
    """
    return reconcile_page(a_scope(seed_user), agreement, tab)


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


class TestAnUnmatchedInflowIsNeverPreFilled:
    """Ruling **bank_import:R-HX**, which bounds **R-HS**.

    The only inflow door records uncategorized INCOME, and being the ONLY act
    is not a justification: a merchant credit is a refund, and filing one as
    income is the wrong act ``X-gj-2`` exists to correct.  On the developer's
    own account 16 of the 18 inbox lines are deposits.
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
            a_bars(seed_user),
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
            a_bars(seed_user),
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
