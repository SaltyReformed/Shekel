"""What the GRID's bank door counts, and why it is not simply "unmatched".

The control the grid renders beside the anchor balance carries a count of the
lines the review has not disposed of, and that figure links to the review
screen.  A figure and its caption may not disagree (the design language's
second principle), so the count applies exactly the predicates
:func:`~app.services.statement_match.review_set` splits on and no others.
There are THREE since plan step balance:X-f3c-2b-2b added the books bound;
this header said "two" until that step, which is the count going stale where
nothing reads it.

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
    a_scope,
    a_submission,
    a_transaction,
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


class TestTheCountTheGridRenders:
    """The two predicates, and the boundary between them."""

    def test_an_account_with_nothing_recorded_counts_zero(
        self, app, db, seed_user,
    ):
        """The state the developer is in before the first import.

        The door still renders at zero -- that is the template's rule, and
        the reason for it is that a control appearing only once lines exist
        cannot be found by someone who has never imported one.
        """
        assert awaiting_review_count(seed_user["account"].id, _opens(seed_user)) == 0

    def test_an_unmatched_line_inside_the_calendar_counts(
        self, app, db, seed_user,
    ):
        """The ordinary case: the bank said something nobody has filed yet."""
        a_bank_line(seed_user, an_import(seed_user))

        assert awaiting_review_count(seed_user["account"].id, _opens(seed_user)) == 1

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

        assert awaiting_review_count(seed_user["account"].id, opens) == 0

    def test_a_line_ON_the_opening_day_IS_counted(self, app, db, seed_user):
        """The boundary is inclusive, matching ``_split_at_calendar_open``.

        Separated from the case above deliberately: a ``>`` written for a
        ``>=`` passes every test that only ever probes a day either side.
        """
        opens = _opens(seed_user)
        a_bank_line(seed_user, an_import(seed_user), posted_on=opens)

        assert awaiting_review_count(seed_user["account"].id, opens) == 1

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

        assert awaiting_review_count(seed_user["account"].id, None) == 1

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
        assert awaiting_review_count(seed_user["account"].id, opens) == 1

        scope = a_scope(seed_user)
        statement_match.accept_match(
            a_submission(scope, lines=[line], transactions=[txn]),
            scope,
        )
        db.session.flush()

        assert awaiting_review_count(seed_user["account"].id, opens) == 0

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

        assert awaiting_review_count(seed_user["account"].id, opens) == 1
        assert awaiting_review_count(other.id, opens) == 1


class TestTheCountAgreesWithTheScreenItLinksTo:
    """The invariant the whole design rests on."""

    def test_it_equals_what_the_review_is_still_asking_about(
        self, app, db, seed_user,
    ):
        """Proposals plus unmatched, counted the expensive way, agree.

        This is the assertion that would catch the count drifting from
        ``review_set`` if either grew a predicate the other did not: it
        derives the truth through the real screen and compares.  A proposal
        is still WORK -- nobody has accepted it -- which is why it is on this
        side of the equals sign.
        """
        statement = an_import(seed_user)
        a_bank_line(seed_user, statement, amount="-180.00", sequence_in_group=0)
        a_bank_line(seed_user, statement, amount="-42.00", sequence_in_group=1)
        a_transaction(seed_user, amount="180.00")

        scope = a_scope(seed_user)
        review = statement_match.review_set(scope)
        still_asking = len(
            {line.line_id for p in review.proposals for line in p.lines}
            | {line.line_id for line in review.unmatched}
        )

        assert still_asking == 2, "fixture should leave both lines as work"
        assert awaiting_review_count(
            seed_user["account"].id, scope.calendar.opening_bound(),
        ) == still_asking
