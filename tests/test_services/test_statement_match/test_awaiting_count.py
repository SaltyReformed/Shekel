"""What the GRID's bank door counts, and why it is not simply "unmatched".

The control the grid renders beside the anchor balance carries a count of the
lines the review is still asking the owner about, and that figure links to the
Reconcile page.  A figure and its caption may not disagree (the design
language's second principle).

**Since plan step bank_import:X-gm the count does not APPLY the review's
predicates -- it is the review's own membership walk**
(``_undisposed.inbox_partition``), which the page is also built from.  That is
``CLAUDE.md`` rule 14: one producer, and every caller reaches it.  Until then
the count spelled four predicates of its own beside the pass's Python, two of
them SQL restatements of it and one -- the holding states -- a predicate the
pass had and the badge did not.  Measured 2026-09-05 at migration head on the
developer's own Checking, the badge read **27** where the page's inbox read
**18**, the difference being 9 parked card payments worth `$7,412.94`; they
agreed only because the badge still opened the retiring review queue, which
rendered all 27.

*This header counted those predicates through three steps that changed them --
"two" until balance:X-f3c-2b-2b, "three" until bank_import:X-gj-4a's skip,
"four" until here -- which is a number going stale where nothing reads it, and
is why there is no number in it now.*

**The calendar bound is what makes this a real gate rather than a spelling of
``COUNT(*)``.**  A line posted before the owner's first payday can never be
matched: there are no rows before that day for it to match to.  A large
fraction of the developer's own export is that shape -- 130 of 361 lines when
that was measured, against 378 lines over two imports on 2026-08-31 -- so a
count that included them would sit permanently non-zero and the badge would
tell the owner nothing.  The FRACTION is the argument; the two absolute
figures are quoted with their dates because the export grows and an undated
one decays where it is read as a reason.
"""

from datetime import timedelta
from decimal import Decimal

from app.services import account_service, statement_match
from app.services.statement_match import awaiting_review_count
from tests._test_helpers import open_books_before_the_first_assertion

from ._builders import (
    a_bank_line,
    a_purchase,
    a_scope,
    a_submission,
    a_transaction,
    an_envelope,
    an_import,
)


def _opens(seed_user):
    """Return the day this owner's seeded pay calendar opens.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The bootstrap period's start day.
    """
    return seed_user["bootstrap_period"].start_date


def _OWNER(seed_user):  # pylint: disable=invalid-name
    """Return the owning user id the count is scoped to.

    Plan step ``bank_import:X-gm`` gave ``awaiting_review_count`` an owner:
    the inbox excludes the lines a source files as paying an account the owner
    holds, and which merchants those are is read beside what the OWNER has
    answered about them (``budget.merchant_rules`` is keyed by user as well as
    account).  Named in capitals so a reader scanning a call sees which
    argument is which at the site rather than having to count positions.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The owning user's id.
    """
    return seed_user["user"].id


class TestTheCountTheGridRenders:
    """The DAY predicates, and the boundary between them.

    *It said "the two predicates" through two steps that added a third and a
    fourth* -- balance:X-f3c-2b-2b's books bound and this step's skip -- so it
    names the KIND now rather than a count that decays where nothing reads it.
    The module header carries the count, in one place.  Named by adversarial
    design review 2026-09-02.
    """

    def test_an_account_with_nothing_recorded_counts_zero(
        self, app, db, seed_user,
    ):
        """The state the developer is in before the first import.

        The door still renders at zero -- that is the template's rule, and
        the reason for it is that a control appearing only once lines exist
        cannot be found by someone who has never imported one.
        """
        assert awaiting_review_count(
            _OWNER(seed_user), seed_user["account"].id, _opens(seed_user),
        ) == 0

    def test_an_unmatched_line_inside_the_calendar_counts(
        self, app, db, seed_user,
    ):
        """The ordinary case: the bank said something nobody has filed yet."""
        a_bank_line(seed_user, an_import(seed_user))

        assert awaiting_review_count(
            _OWNER(seed_user), seed_user["account"].id, _opens(seed_user),
        ) == 1

    def test_a_line_BEFORE_the_calendar_opens_is_not_counted(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the predicate that keeps the badge meaningful.

        Delete the ``posted_on >= opens`` filter and this reads 1.  On the
        developer's own statement it would read 130 forever, because no row
        exists before the first payday for those lines to match to -- so the
        badge would never clear and would stop being read at all.
        """
        opens = _opens(seed_user)
        a_bank_line(
            seed_user, an_import(seed_user),
            posted_on=opens - timedelta(days=1),
        )

        assert awaiting_review_count(
            _OWNER(seed_user), seed_user["account"].id, opens,
        ) == 0

    def test_a_line_ON_the_opening_day_IS_counted(self, app, db, seed_user):
        """The boundary is inclusive, matching ``_split_at_calendar_open``.

        Separated from the case above deliberately: a ``>`` written for a
        ``>=`` passes every test that only ever probes a day either side.
        """
        opens = _opens(seed_user)
        a_bank_line(seed_user, an_import(seed_user), posted_on=opens)

        assert awaiting_review_count(
            _OWNER(seed_user), seed_user["account"].id, opens,
        ) == 1

    def test_an_owner_with_NO_calendar_counts_every_unmatched_line(
        self, app, db, seed_user,
    ):
        """``opens=None`` puts nothing before the calendar.

        ``_split_at_calendar_open``'s own rule: "before the calendar" is not
        a fact about a calendar that does not exist, so the lines are work
        rather than out of reach.  A count that treated ``None`` as "exclude
        everything" would silently read 0 for such an owner.

        **The books are opened before the line first** (plan step
        balance:X-f3c-2b-2b).  The seeded account's books open the day before
        the bootstrap period, so a line 400 days earlier is inside its opening
        equity and the THIRD predicate would exclude it whatever ``opens``
        said -- and this case would then pass while measuring nothing about
        the calendar arm at all.
        """
        day = _opens(seed_user) - timedelta(days=400)
        open_books_before_the_first_assertion(
            db.session, seed_user["account"], also_before=day,
        )
        a_bank_line(seed_user, an_import(seed_user), posted_on=day)

        assert awaiting_review_count(
            _OWNER(seed_user), seed_user["account"].id, None,
        ) == 1

    def test_a_line_an_accepted_match_names_is_not_counted(
        self, app, db, seed_user,
    ):
        """Accepting is what disposes of a line, and the count has to follow.

        Asserted on BOTH sides of the accept, so a count that was always 0
        cannot pass: the pre-assert is what makes the post-assert mean
        something.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        txn = a_transaction(seed_user, amount="180.00")
        opens = _opens(seed_user)
        assert awaiting_review_count(
            _OWNER(seed_user), seed_user["account"].id, opens,
        ) == 1

        scope = a_scope(seed_user)
        statement_match.accept_match(
            a_submission(scope, lines=[line], transactions=[txn]),
            scope,
        )
        db.session.flush()

        assert awaiting_review_count(
            _OWNER(seed_user), seed_user["account"].id, opens,
        ) == 0

    def test_it_counts_ONE_account(self, app, db, seed_user):
        """The grid renders one account's door; it may not total the owner's.

        Two accounts, one line each: a count missing its ``account_id``
        filter reads 2 on a door that links to a screen showing 1.
        """
        other = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=seed_user["account"].account_type_id,
                name="Second Checking",
                anchor_balance=Decimal("0.00"),
            ),
        )
        db.session.flush()
        # A factory-fresh account opens its books TODAY (ruling R-HG), and the
        # line below is dated at the bootstrap period -- so without this the
        # second account's line sits inside its own opening equity and the
        # count reads 0 for a reason that has nothing to do with scoping.
        open_books_before_the_first_assertion(db.session, other)
        a_bank_line(seed_user, an_import(seed_user))
        a_bank_line(seed_user, an_import(seed_user, account=other), amount="-9.99")
        opens = _opens(seed_user)

        assert awaiting_review_count(
            _OWNER(seed_user), seed_user["account"].id, opens,
        ) == 1
        assert awaiting_review_count(_OWNER(seed_user), other.id, opens) == 1


class TestASkippedLineIsNotWork:
    """Plan step ``bank_import:X-gj-4a``, the FOURTH predicate.

    The badge asks *how many lines still have no answer*, and *explained by
    nothing* is an answer (ruling **R-HP**).  A badge that went on counting a
    skipped line would send the owner to a screen that no longer holds it --
    the figure-and-caption disagreement this module exists to refuse.
    """

    def test_a_skipped_line_is_not_counted(self, app, db, seed_user):
        """FIRING CONTROL: drop the skip term and this reads 1.

        The pair matters: 1 before and 0 after, from ONE line, so the case
        cannot pass against a count that was zero for some other reason.
        """
        opens = _opens(seed_user)
        line = a_bank_line(seed_user, an_import(seed_user))
        db.session.flush()
        account_id = seed_user["account"].id
        assert awaiting_review_count(_OWNER(seed_user), account_id, opens) == 1

        statement_match.skip_line(line.id, seed_user["user"].id, account_id)

        assert awaiting_review_count(_OWNER(seed_user), account_id, opens) == 0

    def test_undoing_the_skip_makes_it_work_again(self, app, db, seed_user):
        """The question comes back, so the badge does too."""
        opens = _opens(seed_user)
        line = a_bank_line(seed_user, an_import(seed_user))
        db.session.flush()
        account_id = seed_user["account"].id
        recorded = statement_match.skip_line(
            line.id, seed_user["user"].id, account_id,
        )

        statement_match.unskip_line(
            recorded.skip_id, seed_user["user"].id, account_id,
        )

        assert awaiting_review_count(_OWNER(seed_user), account_id, opens) == 1


class TestTakingTheHoldingStatesOffTheProposerCostsNoACT:
    """Plan step ``bank_import:X-gm``: what the split must NOT have broken.

    The walk hands the proposer only the inbox, so a parked card payment is
    never proposed.  Ruling **R-GJ** leaves the hand-built GROUP MATCH as such
    a line's only arm, and that arm is reached off
    :attr:`~._reads.ReviewSet.unmatched` and through
    :meth:`~._reads.ReviewSet.card_subject` -- so the line has to rejoin that
    list even though it left the count.  Losing it there would take the last
    act a `$7,412.94` class has, which is the fail-closed shape ruling
    **R-KA** refused to mint one surface over.
    """

    def _a_card_payment(self, seed_user):
        """Stage one line a source files as paying an account the owner holds."""
        return a_bank_line(
            seed_user, an_import(seed_user), amount="-793.23",
            merchant="Capital One Credit Card",
            source_category="Financial Services/Credit Card Payment",
        )

    def test_the_parked_line_is_still_in_unmatched(self, app, db, seed_user):
        """It is out of the COUNT and in the LIST, which are different sets."""
        line = self._a_card_payment(seed_user)

        review = statement_match.review_set(a_scope(seed_user))

        assert [one.line_id for one in review.unmatched] == [line.id]
        assert [one.line.line_id for one in review.parked] == [line.id]

    def test_its_MATCH_pane_still_resolves(self, app, db, seed_user):
        """The route's own membership question, asked the way the route asks it.

        ``accounts.statement_reconcile_match`` answers ``card_subject`` and
        404s on ``None``.  A parked line dropped from ``unmatched`` would 404
        there -- indistinguishable from someone else's line, which is the
        confusion :meth:`~._reads.ReviewSet.card_subject`'s own docstring
        measured at 137 of 137 cards.
        """
        line = self._a_card_payment(seed_user)

        subject = statement_match.review_set(a_scope(seed_user)).card_subject(
            line.id,
        )

        assert subject is not None
        assert subject.line.line_id == line.id
        assert subject.proposal is None, (
            "a holding state was proposed a match, which the walk exists to "
            "stop"
        )

    def test_taking_a_line_off_the_proposer_FREES_ITS_ROW(
        self, app, db, seed_user,
    ):
        """The consequence that is not about the parked line at all.

        ``propose`` is a cascade over a SHARED row pool, and
        ``_least_cost_pairing`` is a least-cost pairing over all of it -- so
        removing a line does not only remove ITS proposal, it changes which
        line the freed row is paired with.

        **The fixture is built so the two trees answer differently.**  The row
        settles on the card payment's own day, so ``days_outside`` scores that
        pairing zero and the ordinary swipe three; before the split the row
        went to the card payment and the swipe stayed unexplained, and after it
        the card payment is never offered and the swipe is PROPOSED.  A fixture
        with both lines on one day would score both zero and decide on the
        tie-break, which would grade the ordering rather than the split.
        """
        statement = an_import(seed_user)
        day = _opens(seed_user) + timedelta(days=4)
        envelope = an_envelope(seed_user)
        a_purchase(
            seed_user, envelope, amount="793.23", description="Capital One",
            purchased_on=day, settled_on=day,
        )
        card_payment = a_bank_line(
            seed_user, statement, amount="-793.23", posted_on=day,
            sequence_in_group=0, merchant="Capital One Credit Card",
            source_category="Financial Services/Credit Card Payment",
        )
        swipe = a_bank_line(
            seed_user, statement, amount="-793.23", sequence_in_group=1,
            posted_on=day + timedelta(days=3), merchant="Kroger",
        )
        db.session.commit()

        review = statement_match.review_set(a_scope(seed_user))

        proposed = [
            line.line_id
            for one in review.proposals for line in one.lines
        ]
        assert proposed == [swipe.id], (
            "the freed row did not reach the line the split leaves offerable"
        )
        assert [one.line.line_id for one in review.parked] == [
            card_payment.id,
        ]

    def test_the_list_is_still_ascending_by_posted_day(
        self, app, db, seed_user,
    ):
        """The parked lines rejoin in ORDER, not appended after the rest.

        ``unmatched`` is documented ascending by day and the workbench renders
        it in that order; appending the parked lines would put an April card
        payment after an August swipe.
        """
        statement = an_import(seed_user)
        opens = _opens(seed_user)
        a_bank_line(
            seed_user, statement, amount="-793.23", sequence_in_group=0,
            posted_on=opens, merchant="Capital One Credit Card",
            source_category="Financial Services/Credit Card Payment",
        )
        a_bank_line(
            seed_user, statement, amount="-42.00", sequence_in_group=1,
            posted_on=opens + timedelta(days=2),
        )

        review = statement_match.review_set(a_scope(seed_user))

        days = [one.posted_on for one in review.unmatched]
        assert len(days) == 2, "both lines must reach the list"
        assert days == sorted(days)
        assert days[0] == opens


class TestTheCountAgreesWithTheScreenItLinksTo:
    """The invariant the whole design rests on, plan step ``bank_import:X-gm``.

    **The page's own figure is the right side of the equals sign now, not a
    re-derivation of it.**  This class asserted ``proposals | unmatched``,
    which is the union the badge used to be compared against -- and that union
    is exactly what parted the two: it holds the parked card payments, which
    are on the Transfers tab and are not work.  A case that kept it would be
    grading the old disagreement as the invariant.
    """

    def test_the_badge_equals_the_page_it_links_to(
        self, app, db, seed_user,
    ):
        """The number the grid shows IS the number the inbox shows.

        Compared against ``page.hero.to_explain`` -- which is the count of the
        cards the inbox tab renders -- rather than against a sum this case
        works out for itself.  A case that re-derived the right-hand side
        would be a third spelling of the value under test, which is the shape
        this step exists to delete.

        **The fixture stages all three terms**, and each is asserted present:
        an ordinary unexplained line, a line a tier PROPOSES a match for, and a
        card payment the pass PARKS.  With any of the three missing the
        equality reduces to a claim about a shorter sum -- and the parked one
        is the term that made the two numbers differ at all, so a fixture
        without it would have passed on the broken tree.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-180.00", sequence_in_group=0)
        a_bank_line(seed_user, statement, amount="-42.00", sequence_in_group=1)
        a_bank_line(
            seed_user, statement, amount="-793.23", sequence_in_group=2,
            merchant="Capital One Credit Card",
            source_category="Financial Services/Credit Card Payment",
        )
        a_transaction(seed_user, amount="180.00")

        scope = a_scope(seed_user)
        review = statement_match.review_set(scope)
        page = statement_match.reconcile_page(
            scope, None, statement_match.Tab.TO_EXPLAIN,
        )

        assert review.proposals, "no proposal staged: one term of three"
        assert review.parked, "no parked line staged: the term that DIFFERED"
        assert page.hero.to_explain == 2
        assert awaiting_review_count(
            _OWNER(seed_user),
            seed_user["account"].id,
            scope.calendar.opening_bound(),
        ) == page.hero.to_explain

    def test_the_parked_line_is_on_the_page_and_out_of_the_count(
        self, app, db, seed_user,
    ):
        """The other side, so the equality above is not agreeing about nothing.

        Without it a badge and a page that BOTH dropped the parked line -- or
        both counted it -- would satisfy the equality while getting the
        Transfers tab wrong.  The line has to be absent from the figure and
        present on the page.
        """
        a_bank_line(
            seed_user, an_import(seed_user), amount="-793.23",
            merchant="Capital One Credit Card",
            source_category="Financial Services/Credit Card Payment",
        )

        scope = a_scope(seed_user)
        page = statement_match.reconcile_page(
            scope, None, statement_match.Tab.TRANSFERS,
        )

        assert awaiting_review_count(
            _OWNER(seed_user),
            seed_user["account"].id,
            scope.calendar.opening_bound(),
        ) == 0
        assert [
            count.count for count in page.counts
            if count.tab is statement_match.Tab.TRANSFERS
        ] == [1]
