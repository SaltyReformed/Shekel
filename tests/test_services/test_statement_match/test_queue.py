"""The exception queue as ONE list grouped by the decision each line poses.

Ruling **bank_import:R-HB**, plan step ``bank_import:X-gf-3b-2``.  The review
screen used to partition its unexplained lines by MECHANISM across three cards;
it groups them by what this pass's own EVIDENCE says instead, and each row still
carries whatever act its mechanism opens.

**What these cases grade that the route tests cannot**: which GROUP a line
lands in is the whole claim this step makes about it, and the group is decided
from two signals that are not exclusive -- a positive counterpart signal, and a
search this pass could not finish.  A case that only asserted "the line is on
the page" would pass for a queue that put every line in one group.

**The conservation case is the one that catches a whole class**, and the
identity is not the obvious one.  A line must appear at most ONCE across the
groups -- twice would render two controls against one movement -- but the
groups do not cover ``unmatched``: an outflow the bank dates MADE after it
POSTED reaches none of the three mechanisms (finding **N-325**), so it is in
``unmatched``, in no group, and disclosed as
``ReviewBounds.impossible_day_count`` instead.  The conserved sum is therefore
``grouped + impossible_day_count == unmatched``.

*A first version of this module asserted the simpler identity and an
adversarial review measured it FALSE by staging that one line shape: the queue
rendered ZERO groups for a statement holding one unexplained line, and no case
here varied the axis that would have shown it.*
"""

from datetime import timedelta
from decimal import Decimal

from app.enums import StatusEnum
from app.services.statement_match import (
    Evidence,
    review_set,
)

from ._builders import (
    a_bank_line,
    a_purchase,
    a_rule,
    a_scope,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)

#: What SECU files a card payment under, which ruling **R-GJ** reads.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _groups(review):
    """Return the queue's groups keyed by their evidence.

    Args:
        review: The assembled :class:`~app.services.statement_match.ReviewSet`.

    Returns:
        A dict of :class:`~app.services.statement_match.Evidence` to group.
    """
    return {group.evidence: group for group in review.queue.groups}


class TestWhichGroupALineLandsIn:
    """The grouping predicate, one case per answer the evidence can give."""

    def test_a_BARRED_line_is_grouped_as_already_held(
        self, app, db, seed_user,
    ):
        """Ruling **R-GJ**'s bar IS the positive counterpart signal.

        Both its arms say the money is not new spending -- a merchant a source
        files as a payment to an account the owner holds, and one they have
        answered is never a purchase -- and the remedy both leave is the group
        match.  Measured on the developer's own data 2026-08-28: 9 of his 9
        parked lines.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        groups = _groups(review_set(a_scope(seed_user)))

        assert list(groups) == [Evidence.ALREADY_HELD]
        row = groups[Evidence.ALREADY_HELD].rows[0]
        assert row.offers_no_control is True
        assert row.records_a_purchase is False
        assert row.records_income is False

    def test_an_ORDINARY_swipe_the_pass_settled_is_grouped_as_nothing_found(
        self, app, db, seed_user,
    ):
        """The state that makes recording safe.

        No bar, no income the books already hold for its period, and a search
        that finished: the app looked and found nothing, so the line carries no
        sentence at all and its group says recording ADDS what was missing.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        db.session.commit()

        groups = _groups(review_set(a_scope(seed_user)))

        assert list(groups) == [Evidence.NOTHING_FOUND]
        row = groups[Evidence.NOTHING_FOUND].rows[0]
        assert row.records_a_purchase is True
        assert row.notes == ()

    def test_a_line_whose_SEARCH_DID_NOT_FINISH_is_grouped_apart(
        self, app, db, seed_user,
    ):
        """The group that is EMPTY on the developer's own data.

        It is built because the predicate is real and the data is not: all five
        of his gap-carrying lines happen to also carry a positive signal, which
        is a fact about one statement rather than about the shape.  33 rows
        share the line's own day against a bound of 32, so the group search
        skips it and the pass cannot say the line has no counterpart.
        """
        day = seed_user["bootstrap_period"].start_date
        an_envelope(seed_user)
        for index in range(33):
            a_transaction(
                seed_user, name=f"Bill {index}", amount=f"{index + 11}.00",
                status=StatusEnum.DONE, settled_on=day,
            )
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        db.session.commit()

        groups = _groups(review_set(a_scope(seed_user)))

        assert list(groups) == [Evidence.UNFINISHED]
        row = groups[Evidence.UNFINISHED].rows[0]
        assert any(
            "held too many rows for the app to search them" in note
            for note in row.notes
        )

    def test_a_POSITIVE_signal_outranks_an_unfinished_search(
        self, app, db, seed_user,
    ):
        """The two signals are not exclusive, and the order is the risk order.

        Grouping a line the books may already hold under *the app could not
        finish looking* would state the weaker of two true things about it, and
        the weaker one reads as safer.  On the developer's own data every one
        of the five gap-carrying lines is this case.
        """
        day = seed_user["bootstrap_period"].start_date
        an_envelope(seed_user)
        for index in range(33):
            a_transaction(
                seed_user, name=f"Bill {index}", amount=f"{index + 11}.00",
                status=StatusEnum.DONE, settled_on=day,
            )
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))
        groups = _groups(review)

        assert review.search_gap_for(review.parked[0].line) is not None
        assert list(groups) == [Evidence.ALREADY_HELD]


class TestEveryLineIsGroupedExactlyOnce:
    """Conservation, which is the case that catches a whole class."""

    def test_the_groups_partition_the_unexplained_lines(
        self, app, db, seed_user,
    ):
        """A line in no group vanished; a line in two renders two controls.

        Three mechanisms reach the queue and all three are staged here, so the
        count is over a set that actually spans them rather than one the
        builder happens to make uniform.
        """
        an_envelope(seed_user)
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=day,
            merchant="Amazon", sequence_in_group=0,
        )
        a_bank_line(
            seed_user, statement, amount="-793.23", posted_on=day,
            merchant="Capital One Credit Card", sequence_in_group=1,
            source_category=_CARD_PAYMENT,
        )
        a_bank_line(
            seed_user, statement, amount="41.10", posted_on=day,
            description="DIVIDEND EARNED", sequence_in_group=2,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        grouped = [row for group in review.queue.groups for row in group.rows]
        assert (
            len(grouped) + review.bounds.impossible_day_count
            == len(review.unmatched)
        )
        assert (
            sorted(row.line.line_id for row in grouped)
            == sorted(line.line_id for line in review.unmatched)
        )
        # No line is grouped twice, which is the half a sum cannot see.
        assert len({row.line.line_id for row in grouped}) == len(grouped)
        # Every mechanism reached the queue, so the count above spans them.
        assert sum(1 for row in grouped if row.records_a_purchase) == 1
        assert sum(1 for row in grouped if row.offers_no_control) == 1
        assert sum(1 for row in grouped if row.records_income) == 1

    def test_a_group_with_no_rows_is_ABSENT_rather_than_empty(
        self, app, db, seed_user,
    ):
        """A heading over no rows reads as work waiting somewhere."""
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert [group.evidence for group in review.queue.groups] == [
            Evidence.NOTHING_FOUND,
        ]

    def test_a_pass_with_nothing_unexplained_has_no_groups(
        self, app, db, seed_user,
    ):
        """``any`` is what the template asks before rendering the queue."""
        an_envelope(seed_user)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert review.queue.groups == ()


class TestTheOtherWithholdingArm:
    """The arm ``_evidence_for`` did not ask about, measured 2026-08-28.

    :func:`~._verdict.ruled` sets a creatable line's ``warning`` from TWO
    arms and only one is the search gap.  The other is
    ``_ALREADY_EXPLAINED``: the rule's own destination is a row this
    statement explains AS A WHOLE, so filing a purchase inside it makes that
    match impossible to accept and leaves the bank line it explained
    unexplained.  A first build read the gap alone, which dropped such a line
    into ``NOTHING_FOUND`` -- the one group that offers a one-click -- under a
    heading saying nothing accounted for it.  **Two independent adversarial
    reviews found it and neither the service nor the route suite could see
    it.**
    """

    @staticmethod
    def _a_collision(seed_user, db):
        """Stage a rule whose destination this statement explains as a whole.

        The envelope carries one `$180.00` purchase, so its own cash leg is
        `-$180.00` and a bank line of that figure pairs with it one-to-one.
        The Amazon swipe beside it is what the rule reaches.

        Args:
            seed_user: The seeded user bundle.
            db: The session, committed here so the pass reads it.

        Returns:
            The staged envelope.
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
        db.session.commit()
        return envelope

    def test_an_ALREADY_EXPLAINED_line_is_grouped_as_already_held(
        self, app, db, seed_user,
    ):
        """It is a counterpart the pass FOUND, not one it failed to look for.

        So the group is ``ALREADY_HELD`` and never ``UNFINISHED``: the search
        finished, and what it found is the collision the warning names.
        """
        self._a_collision(seed_user, db)

        review = review_set(a_scope(seed_user))
        swipe = next(
            item for item in review.creatable
            if item.line.merchant == "Amazon"
        )

        # The arm really is the non-gap one, which is what makes this the case
        # the first build missed rather than a second spelling of the gap.
        assert swipe.warning is not None
        assert review.search_gap_for(swipe.line) is None
        assert _groups(review)[Evidence.ALREADY_HELD].rows
        assert Evidence.NOTHING_FOUND not in _groups(review)

    def test_it_is_swept_by_NOTHING(
        self, app, db, seed_user,
    ):
        """**The money case.**

        One click filed a purchase into the envelope, which made the proposal
        impossible to accept and left the bank line it explained unexplained.
        The placement is still offered on the line's own select -- nothing is
        taken from an owner who looks -- and no bulk control reaches it.
        """
        self._a_collision(seed_user, db)

        review = review_set(a_scope(seed_user))
        swipe = next(
            item for item in review.creatable
            if item.line.merchant == "Amazon"
        )

        # The sweep provably WOULD have existed: the placement resolves and
        # carries a class, so the negative below is a suppression rather than
        # an absence.
        assert swipe.placement.sweep_class == "into_open"
        assert all(group.sweeps == () for group in review.queue.groups)



class TestNoSweptRowCarriesASentence:
    """The invariant that keeps the ONE grouping decision sufficient.

    Only :attr:`Evidence.NOTHING_FOUND` is given sweeps, and a creatable row
    reaches that group exactly when its ``warning`` is ``None`` -- so a swept
    row has nothing said against it BY CONSTRUCTION.  A first fix defended
    that with a second ``or row.notes`` test inside ``_sweeps_for``, and a
    mutation run measured that **no test could kill it**: with the grouping
    correct, no input reaches it.  A branch nothing can falsify is a fence, so
    it was deleted and this took its place.

    **What this fires on**: a THIRD withholding arm added to
    :func:`~._verdict.ruled` without :func:`~._queue._positive_for` learning
    about it.  Such a line would carry a sentence, group as
    ``NOTHING_FOUND``, and be ticked by the one-click -- which is precisely
    how the ``_ALREADY_EXPLAINED`` arm was missed, and what two adversarial
    reviews had to find by hand because nothing here could.
    """

    @staticmethod
    def _swept_rows(review):
        """Return every row sitting in a group that offers a sweep.

        Args:
            review: The assembled pass.

        Returns:
            The rows a one-click could reach.
        """
        return [
            row for group in review.queue.groups if group.sweeps
            for row in group.rows
        ]

    def test_a_line_the_rules_withheld_is_never_in_a_swept_group(
        self, app, db, seed_user,
    ):
        """Both withholding arms at once, so neither can pass by absence."""
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
        db.session.commit()

        review = review_set(a_scope(seed_user))

        warned = [
            row for group in review.queue.groups for row in group.rows
            if row.notes
        ]
        assert warned, "the fixture must produce a warned row to grade"
        assert [row for row in self._swept_rows(review) if row.notes] == []

    def test_it_holds_when_a_swept_group_actually_EXISTS(
        self, app, db, seed_user,
    ):
        """The case above is vacuous if nothing is swept; this one is not.

        A clean line and a withheld one in the same pass, so the assertion
        runs against a non-empty swept set rather than passing because no
        group offered a click at all.
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
        # A SECOND merchant whose rule reaches a different, uncollided
        # envelope: this one is genuinely clean and is what the sweep counts.
        clean = a_transaction(
            seed_user, name="Fuel", amount="200.00", is_envelope=True,
        )
        an_unexplained_outflow(
            seed_user, merchant="Shell", amount="-41.10", sequence=4,
        )
        a_rule(seed_user, "Shell", template_id=clean.template_id)
        db.session.commit()

        review = review_set(a_scope(seed_user))

        swept = self._swept_rows(review)
        assert swept, "a swept group must exist or this grades nothing"
        assert [row for row in swept if row.notes] == []


class TestALineNoMECHANISMReaches:
    """Finding **N-325**: the class that is in ``unmatched`` and no group."""

    def test_an_impossible_day_line_is_counted_on_the_BOUNDS_instead(
        self, app, db, seed_user,
    ):
        """The queue owes it no row, and the page still owes it a sentence.

        ``_leftovers._creatable_lines`` drops an outflow the bank dates MADE
        after it POSTED, so it reaches none of the three mechanisms.  It stays
        in ``unmatched`` and is disclosed as ``impossible_day_count``.  The
        conserved sum is what this pins, because a reader asserting the
        simpler identity would be wrong by exactly this class.
        """
        day = seed_user["bootstrap_period"].start_date
        an_envelope(seed_user)
        a_bank_line(
            seed_user, an_import(seed_user), amount="-57.96", posted_on=day,
            transaction_on=day + timedelta(days=3), merchant="Amazon",
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))

        assert len(review.unmatched) == 1
        assert review.queue.groups == ()
        assert review.bounds.impossible_day_count == 1
        grouped = [row for group in review.queue.groups for row in group.rows]
        assert (
            len(grouped) + review.bounds.impossible_day_count
            == len(review.unmatched)
        )


class TestTheSweepReachesOnlyTheGroupThatOffersIt:
    """Developer ruling 2026-08-28, generalising ruling **R-FZ(c)**.

    The sweep used to be counted over every creatable line with a placement and
    blind to what the evidence said about it, so the screen could print *the app
    found a row this might be* beside a line and still tick it under a one-click
    that ignored the sentence.  That is the warning-paragraph-above-a-working-
    control shape ruling **R-GJ** cost `$7,412.94` to learn, at the grain of one
    line rather than one merchant.
    """

    def test_a_settled_line_is_swept_in_the_nothing_found_group(
        self, app, db, seed_user,
    ):
        """The class that keeps its one-click, and the count it promises."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        groups = _groups(review_set(a_scope(seed_user)))

        sweeps = groups[Evidence.NOTHING_FOUND].sweeps
        assert [(one.css_class, one.count) for one in sweeps] == [
            ("into_open", 1),
        ]

    def test_a_line_the_pass_COULD_NOT_SETTLE_is_swept_by_nothing(
        self, app, db, seed_user,
    ):
        """**The defect this ruling closes**, and it is money.

        The same rule, the same merchant, the same placement -- and a crowded
        day that stops the pass concluding.  Before this step the line still
        rode the one-click; now its group offers none, and the line keeps its
        own select so nothing is taken away from an owner who looks.
        """
        day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        for index in range(33):
            a_transaction(
                seed_user, name=f"Bill {index}", amount=f"{index + 11}.00",
                status=StatusEnum.DONE, settled_on=day,
            )
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        review = review_set(a_scope(seed_user))
        groups = _groups(review)

        # The placement is still there -- what changed is that no bulk click
        # reaches it.
        assert review.creatable[0].placement.sweep_class == "into_open"
        assert groups[Evidence.UNFINISHED].sweeps == ()
        assert all(group.sweeps == () for group in review.queue.groups)


class TestEverySentenceALineIsOwed:
    """One composition for all three mechanisms.

    Each of the three cards this replaced composed its own evidence sentences
    in Jinja, so a line got whichever its card knew about -- which is how a
    PARKED line came to be the one kind that never printed its search gap.
    """

    def test_a_PARKED_line_now_prints_its_search_gap(
        self, app, db, seed_user,
    ):
        """The asymmetry ruling **bank_import:R-HB** names, closed.

        Measured on the developer's own data 2026-08-28: his `-$1,000.44` line
        of 2026-06-01, 1 of 9 parked, carried its bar reason on the queue and
        its gap only on the workbench.
        """
        day = seed_user["bootstrap_period"].start_date
        an_envelope(seed_user)
        for index in range(33):
            a_transaction(
                seed_user, name=f"Bill {index}", amount=f"{index + 11}.00",
                status=StatusEnum.DONE, settled_on=day,
            )
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        review = review_set(a_scope(seed_user))
        row = _groups(review)[Evidence.ALREADY_HELD].rows[0]

        assert any(
            "payment to an account you hold" in note
            for note in row.notes
        )
        assert any(
            "held too many rows for the app to search them" in note
            for note in row.notes
        )

    def test_a_CREATABLE_line_states_its_gap_ONCE(
        self, app, db, seed_user,
    ):
        """``ruled`` has already folded the gap into ``warning``.

        Asking again would print the same words twice on the one mechanism
        whose value already carries them.
        """
        day = seed_user["bootstrap_period"].start_date
        an_envelope(seed_user)
        for index in range(33):
            a_transaction(
                seed_user, name=f"Bill {index}", amount=f"{index + 11}.00",
                status=StatusEnum.DONE, settled_on=day,
            )
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        db.session.commit()

        review = review_set(a_scope(seed_user))
        row = _groups(review)[Evidence.UNFINISHED].rows[0]

        printed = sum(
            note.count("held too many rows for the app to search them")
            for note in row.notes
        )
        assert printed == 1


class TestTheRowsThisStatementNeverShowed:
    """Finding **bank_import:N-380**: the other side of the reconciliation."""

    def test_the_two_directions_are_counted_APART(
        self, app, db, seed_user,
    ):
        """A caption naming one direction is wrong about the other.

        The workbench claimed of all of them *a payment your records claim
        happened and your bank did not make*, over a list that is 17 deposits
        in 49 of the developer's own rows.
        """
        # NO envelope here, and that is the fixture stating only what it
        # means: an envelope is itself a row the statement never showed, so
        # one staged for scenery would sit in `payments` and the assertion
        # below would be graded on the builder rather than on the partition.
        #
        # A line the pass holds, so the statement covers a span these rows
        # fall inside: `unmatched_rows` is bounded by that span, and a pass
        # holding no line at all covers nothing.
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        a_transaction(
            seed_user, name="Water Bill", amount="88.00",
            status=StatusEnum.DONE,
            settled_on=seed_user["bootstrap_period"].start_date,
        )
        a_transaction(
            seed_user, name="Data Manager", amount="2473.38", income=True,
            status=StatusEnum.DONE,
            settled_on=seed_user["bootstrap_period"].start_date,
        )
        db.session.commit()

        never = review_set(a_scope(seed_user)).rows_never_shown

        assert [row.label for row in never.payments] == ["Water Bill"]
        assert [row.label for row in never.deposits] == ["Data Manager"]
        assert never.payments_total == Decimal("88.00")
        assert never.deposits_total == Decimal("2473.38")
        assert never.any is True

    def test_a_pass_that_explains_every_row_says_nothing(
        self, app, db, seed_user,
    ):
        """``any`` is what the queue asks, so silence is the empty state."""
        an_envelope(seed_user)
        db.session.commit()

        never = review_set(a_scope(seed_user)).rows_never_shown

        assert never.payments == ()
        assert never.deposits == ()
        assert never.any is False
