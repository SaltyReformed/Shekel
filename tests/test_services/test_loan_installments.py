"""The loan payment feed's DATE half: ``app.services.loan_ledger._installments``.

Plan step **balance:X-bl-2a** (finding **N-432**).  Two producers live here and
they are graded on different grounds:

* :func:`~app.services.amortization_engine.schedule_dates` is pure date arithmetic, so
  it is pinned against HAND-COMPUTED slots.  Two of the cases below are the ones
  ``TestPreparePaymentsForEngine`` already states through the public door
  (``test_biweekly_redistribution``, ``test_december_to_january_rollover``);
  those are deliberately NOT moved -- they grade that
  ``prepare_payments_for_engine`` still APPLIES the rule, which is a different
  claim from the rule being right, and both claims need a test.  What is new
  here is the arithmetic no case reached before: a cascade past two allocated
  months, and the day clamp a ``payment_day`` of 31 needs in February.
* :func:`~app.services.loan_ledger.payment_installments` is a loader, so it is
  graded against the query it must not narrow.  The load-bearing case is
  :meth:`TestPaymentInstallments.test_the_two_sets_partition_the_shadow_query`:
  the producer reads the SETTLED set and the PROJECTED set and unions them,
  which is only equal to "every non-excluded shadow" while those two statuses
  exhaust the non-excluded band.  That is a claim about ``ref.statuses``, not
  about this code, so it is asserted over a loan carrying one shadow in EVERY
  status rather than assumed -- a sixth status would fail it.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services.amortization_engine import schedule_dates, slotted_dates
from app.services.loan_ledger import payment_installments
from app.services.loan_loaders import _shadows, query_shadow_income
from app.services.loan_payment_service import get_payment_history, load_loan_context
from app.services.cash_ledger import derived_amount_basis
from app.services.transfer_service import TransferSpec, create_transfer
from tests._test_helpers import (
    an_entered_day,
    create_loan_account,
    loan_params_for,
    settle_day_columns,
    settlement_columns,
)
from app.models.amount_ownership import AmountOwnership

#: The ``payment_day`` of the loans this file builds.
_PAYMENT_DAY = 1


def _make_loan(seed_user):
    """Build a mortgage whose payments are due on the 1st."""
    return create_loan_account(
        seed_user, db.session, name="Installment Mortgage",
        principal=Decimal("250000.00"), rate=Decimal("0.06000"), term=360,
        origination_date=date(2024, 1, 1),
    )


def _transfer_to_loan(
    seed_user, loan, period, amount, status_enum, settled_on=None,
):
    """Create a transfer into *loan*, through the write door.

    Through :func:`~app.services.transfer_service.create_transfer` rather than
    by inserting rows, so the pair of shadows this file reads is the pair
    production writes (Transfer Invariant 1).
    """
    return create_transfer(
        TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=ref_cache.status_id(status_enum),
            category_id=seed_user["categories"]["Rent"].id,
            settle_day=None if settled_on is None else an_entered_day(settled_on),
        ),
    )


def _income_shadow(transfer, loan):
    """Return the loan-side (income) shadow of *transfer*."""
    return (
        db.session.query(Transaction)
        .filter_by(
            transfer_id=transfer.id,
            account_id=loan.id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            is_deleted=False,
        )
        .one()
    )


#: How many shadows the eager-load controls build.  More than one, because the
#: whole distinction they grade is ONE query for the set against one per row.
_SEAM_SHADOWS = 4


def _seam_shadows(seed_user, loan, seed_periods):
    """Create :data:`_SEAM_SHADOWS` payments on *loan*, settled and projected.

    A mixed set, so both halves of the partition are non-empty and the eager
    load has to cover rows from each.
    """
    for index in range(_SEAM_SHADOWS):
        period = seed_periods[index + 1]
        settled = index % 2 == 0
        _transfer_to_loan(
            seed_user, loan, period, Decimal("1500.00"),
            status_enum=StatusEnum.DONE if settled else StatusEnum.PROJECTED,
            settled_on=period.start_date if settled else None,
        )
    db.session.commit()


def _reads_of(table, statements):
    """Return how many of *statements* read *table*.

    The COUNT is the measurement: one statement is an eager load of the whole
    set, N is the per-row walk, zero is no load at all.  Three different facts
    that "did any statement mention it" cannot tell apart.
    """
    return sum(1 for statement in statements if table in statement)


class TestScheduleDates:
    """The monthly slot each payment consumes -- hand-computed."""

    def test_an_uncontested_payment_keeps_its_own_date(self):
        """No collision means no invention: each due date comes back verbatim.

        Three payments due 2026-02-01, 2026-03-01 and 2026-04-01 occupy three
        distinct months, so nothing is reassigned.  This is the shape BOTH live
        loans are in -- 58 shadows, no two sharing a due month (dev, 2026-09-09)
        -- so it is the case that must not invent a date, not merely the easy
        one.
        """
        due = [date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]

        assert schedule_dates(due, _PAYMENT_DAY) == due

    def test_two_payments_due_one_month_take_consecutive_slots(self):
        """The second of two payments due 2026-02-01 moves to 2026-03-01.

        With ``payment_day=1``, pay periods starting 2026-01-02 and 2026-01-16
        both fall before 2026-02-01, so both satisfy the February installment.
        The first keeps February; the second takes the next free month, March,
        on the contractual day.  A monthly engine that saw both in February
        would double-count that month and leave March empty.
        """
        due = [date(2026, 2, 1), date(2026, 2, 1)]

        assert schedule_dates(due, _PAYMENT_DAY) == [
            date(2026, 2, 1), date(2026, 3, 1),
        ]

    def test_a_collision_rolls_over_the_year_boundary(self):
        """Two payments due 2026-12-01: the second takes 2027-01-01.

        The month walk carries into the next YEAR rather than wrapping to month
        13.  **The due month must be DECEMBER for this case to grade that**: an
        earlier draft used two payments due 2027-01-01, whose walk runs January
        to February and never enters the rollover branch at all -- spliced with
        that branch replaced by a raise, the draft still passed while the
        December case raised.  The rollover moved out of the old inline
        ``if m > 12`` into
        :func:`~app.services.amortization_engine.advance_to_next_payment_date`
        at plan step **balance:X-bl-2a**, so it is a branch this file owns the
        only slot-level coverage of.
        """
        due = [date(2026, 12, 1), date(2026, 12, 1)]

        assert schedule_dates(due, _PAYMENT_DAY) == [
            date(2026, 12, 1), date(2027, 1, 1),
        ]

    def test_a_cascade_walks_past_every_allocated_month(self):
        """Four payments due 2026-02-01 fill February, March, April and May.

        The third cannot take March -- the second already has it -- so it walks
        to April, and the fourth to May.  Cascading collisions are not expected
        (at most one extra payment a month, ~2x/year), and this is the case that
        proves the walk does not stop at the first step.
        """
        due = [date(2026, 2, 1)] * 4

        assert schedule_dates(due, _PAYMENT_DAY) == [
            date(2026, 2, 1), date(2026, 3, 1),
            date(2026, 4, 1), date(2026, 5, 1),
        ]

    def test_an_invented_slot_clamps_the_day_to_the_month(self):
        """A ``payment_day`` of 31 colliding into February lands on the 28th.

        Two payments due 2026-01-31; the second is pushed into February, which
        has 28 days in 2026.  ``date(2026, 2, 31)`` does not exist, so the day
        is clamped -- through the same
        :func:`~app.services.amortization_engine.advance_to_next_payment_date`
        a forward projection clamps with, so a loan due on the 31st reads the
        same February date wherever it is asked.
        """
        due = [date(2026, 1, 31), date(2026, 1, 31)]

        assert schedule_dates(due, 31) == [
            date(2026, 1, 31), date(2026, 2, 28),
        ]

    def test_no_payments_means_no_slots(self):
        """An empty feed returns an empty slot list, not an error."""
        assert schedule_dates([], _PAYMENT_DAY) == []


class TestPaymentInstallments:
    """The loader: which rows, in what order, carrying which dates."""

    def test_the_two_sets_partition_the_shadow_query(
        self, app, db, seed_user, seed_periods,
    ):
        """The producer returns EXACTLY the rows ``query_shadow_income`` does.

        **The control this producer's whole shape rests on.**  It reads the
        SETTLED set and the PROJECTED set and unions them, which equals "every
        non-excluded shadow" only while ``Projected`` and the settled statuses
        exhaust the band ``query_shadow_income`` leaves standing.  That is a
        claim about the ``ref.statuses`` seed -- exactly the kind of set defined
        by subtraction that nobody re-censuses -- so a shadow is created in
        EVERY status and the two row sets are compared directly.  Seeding a
        sixth status that is neither projected nor settled nor
        balance-excluded would fail here rather than silently dropping its rows
        from every loan schedule.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            for index, status in enumerate(StatusEnum):
                _transfer_to_loan(
                    seed_user, loan, seed_periods[index], Decimal("1500.00"),
                    status_enum=status,
                    settled_on=(
                        seed_periods[index].start_date
                        if ref_cache.status_id(status) in _settled_ids()
                        else None
                    ),
                )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            queried = {
                shadow.id
                for shadow in query_shadow_income(
                    loan.id, scenario_id, options=(),
                ).all()
            }
            produced = {
                installment.income_shadow.id
                for installment in payment_installments(
                    loan.id, scenario_id, _PAYMENT_DAY, options=(),
                )
            }

            # Non-vacuous in both directions: the query must have kept some
            # rows and dropped some, or "the two sets agree" says nothing.
            assert queried, "query_shadow_income returned nothing to partition"
            assert len(queried) < len(list(StatusEnum)), (
                "no status was excluded -- this case would pass over a "
                "producer that simply read every shadow"
            )
            assert produced == queried

    def test_settled_ness_comes_from_which_set_a_row_arrived_in(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled shadow carries its cash day; a projected one carries none.

        The classification this producer exists to make single: the settled row
        is the one the fold's own loader returned, and its ``settled_on`` is the
        day :func:`~app.services.loan_ledger.payment_visible_on` reads off it --
        the same day the posted ledger counts that payment's principal from.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            settled_day = seed_periods[1].start_date
            _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.DONE, settled_on=settled_day,
            )
            _transfer_to_loan(
                seed_user, loan, seed_periods[2], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            db.session.commit()

            installments = payment_installments(
                loan.id, seed_user["scenario"].id, _PAYMENT_DAY, options=(),
            )

            assert [i.dates.settled_on for i in installments] == [
                settled_day, None,
            ]
            assert [i.dates.period_start for i in installments] == [
                seed_periods[1].start_date, seed_periods[2].start_date,
            ]

    def test_a_projected_row_holding_a_stale_settle_day_reports_none(
        self, app, db, seed_user, seed_periods,
    ):
        """A Projected shadow that still carries a day is NOT confirmed.

        Only a bypass of the status seam can produce that row -- there is
        deliberately no ``CHECK`` for it, because the settled predicate lives in
        ``ref.statuses`` and a constraint cannot join.  The day is written here
        directly, which is the bypass, and the producer must still report the
        payment as not-yet-happened: it arrives in the PROJECTED set, so its day
        is never read at all.  Reporting it would tell the replay an installment
        was paid that the posted ledger has no entry for.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            transfer = _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            db.session.commit()
            shadow = _income_shadow(transfer, loan)
            # The WHOLE settlement record, not just the day.  Three CHECKs weld
            # it -- the day needs its basis, and it needs a settlement record
            # (what moved, and how that figure is known) -- so a fixture that
            # wrote only ``settled_on`` would build a row the database refuses
            # and this case would grade a state nothing can reach.  What no
            # CHECK can say is the STATUS, because the settled predicate lives
            # in ``ref.statuses`` and a constraint cannot join: that is exactly
            # the gap this row sits in.
            bypass = {
                **settle_day_columns(seed_periods[1].start_date),
                **settlement_columns(
                    seed_periods[1].start_date, Decimal("1500.00"),
                ),
            }
            for column, value in bypass.items():
                setattr(shadow, column, value)
            db.session.commit()

            installments = payment_installments(
                loan.id, seed_user["scenario"].id, _PAYMENT_DAY, options=(),
            )

            assert len(installments) == 1
            assert installments[0].income_shadow.settled_on is not None, (
                "the bypass did not land -- this case would pass over a "
                "producer that reads the day and reports it"
            )
            assert installments[0].dates.settled_on is None

    def test_two_payments_in_one_period_order_by_id(
        self, app, db, seed_user, seed_periods,
    ):
        """Same pay period: the lower ``id`` comes first, whichever SET it is in.

        The order reaches exactly one decision --
        :func:`~app.services.amortization_engine.schedule_dates`, where it settles which
        of two payments colliding on a due month keeps it -- and until plan step
        **balance:X-bl-2a** the feed ordered by ``pay_period.start_date`` alone,
        leaving that to whatever order the database returned.

        **The projected row is created FIRST and the settled row SECOND, and
        that is what gives this case teeth.**  The producer reads the settled
        set before the projected one, so the merged list starts out
        settled-first; a sort on ``start_date`` alone is STABLE and would keep
        the higher-id settled row in front.  Only the ``id`` tie-break puts them
        in the order the feed's chronology means.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            earlier = _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            later = _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1600.00"),
                status_enum=StatusEnum.DONE,
                settled_on=seed_periods[1].start_date,
            )
            db.session.commit()
            earlier_id = _income_shadow(earlier, loan).id
            later_id = _income_shadow(later, loan).id
            assert earlier_id < later_id, (
                "the projected row must carry the LOWER id, or the settled-set-"
                "first read order would already put the rows right"
            )

            installments = payment_installments(
                loan.id, seed_user["scenario"].id, _PAYMENT_DAY, options=(),
            )

            assert [i.income_shadow.id for i in installments] == [
                earlier_id, later_id,
            ]

    def test_a_settled_and_a_projected_row_interleave_by_period(
        self, app, db, seed_user, seed_periods,
    ):
        """The two sets merge into ONE chronology, not settled-then-projected.

        A projected payment in an EARLIER period than a settled one must sort
        first: the merged order is the feed's chronology, and it is what decides
        a due-month collision.  Concatenating the settled list and the projected
        list without re-sorting would put the later settled payment first and
        hand it a month the earlier plan should have kept.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            _transfer_to_loan(
                seed_user, loan, seed_periods[3], Decimal("1500.00"),
                status_enum=StatusEnum.DONE,
                settled_on=seed_periods[3].start_date,
            )
            _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            db.session.commit()

            installments = payment_installments(
                loan.id, seed_user["scenario"].id, _PAYMENT_DAY, options=(),
            )

            assert [i.dates.period_start for i in installments] == [
                seed_periods[1].start_date, seed_periods[3].start_date,
            ]
            assert [
                i.dates.settled_on is None for i in installments
            ] == [True, False]

    def test_a_loan_with_no_payments_produces_nothing(
        self, app, db, seed_user,
    ):
        """No shadows means an empty feed, not an error."""
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()

            assert payment_installments(
                loan.id, seed_user["scenario"].id, _PAYMENT_DAY, options=(),
            ) == []


class TestBothFeedsReachOneSlotAssignment:
    """The PRICED feed and the AMOUNT-FREE feed resolve a collision IDENTICALLY.

    **The claim ``slotted_dates`` exists for, and until plan step
    balance:X-bl-2b nothing graded it.**  Ruling **R-BAL7** put the
    biweekly-collision assignment in the pure engine so BOTH of a loan's feeds
    reach one producer -- and the reason is not tidiness: a slot the two do not
    agree on is a month the replay CONSUMES and the forward override PLANS
    separately, which silently drops a planned payment.  The two producers were
    pinned only through their own halves (``TestPreparePaymentsForEngine`` on the
    priced one, :class:`TestScheduleDates` on the arithmetic); nothing ran a
    collision through both and compared, so an edit that slotted one feed and not
    the other would have gone green.

    The case is the smallest collision there is: two payments in ONE pay period,
    so both satisfy the same monthly installment and the second must be pushed
    to the next free month.  **The non-vacuity arms are asserted first** -- that
    the two payments really do share a due month, and that the assignment really
    does move one of them -- because an equality between two feeds that both
    happened to leave the dates alone would pass over the defect this exists to
    catch.
    """

    def test_a_collision_gets_the_same_slots_from_both_feeds(
        self, app, db, seed_user, seed_periods,
    ):
        """Both feeds report the same due dates, and the facts are untouched."""
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            for amount in (Decimal("1500.00"), Decimal("1600.00")):
                _transfer_to_loan(
                    seed_user, loan, seed_periods[1], amount,
                    status_enum=StatusEnum.DONE,
                    settled_on=seed_periods[1].start_date,
                )
            db.session.commit()

            scenario_id = seed_user["scenario"].id
            installments = payment_installments(
                loan.id, scenario_id, _PAYMENT_DAY, options=(),
            )
            amount_free = slotted_dates(
                [installment.dates for installment in installments],
                _PAYMENT_DAY,
            )
            priced = load_loan_context(
                loan.id,
                derived_amount_basis(seed_user["user"].id, scenario_id),
                loan_params_for(db.session, loan.id),
            ).payments

            # Non-vacuity 1: the two payments really do collide.
            assert len(installments) == 2
            assert (
                installments[0].dates.due_date == installments[1].dates.due_date
            ), "the fixture built no collision -- this case grades nothing"
            # Non-vacuity 2: the assignment really does move the loser.
            assert amount_free[1].due_date != installments[1].dates.due_date, (
                "no slot was invented -- an assignment that did nothing would "
                "make the equality below hold trivially"
            )

            # The claim: ONE assignment, reached by both feeds.
            assert [dates.due_date for dates in amount_free] == [
                payment.dates.due_date for payment in priced
            ]
            # ...and only the installment moved on either side.
            assert [dates.period_start for dates in amount_free] == [
                payment.dates.period_start for payment in priced
            ]
            assert [dates.settled_on for dates in amount_free] == [
                payment.dates.settled_on for payment in priced
            ]


def _settled_ids():
    """Return the settled status ids -- the band the producer's settled set is.

    Read through the shared accessor rather than named here, so a case that
    seeds "one shadow per status" cannot disagree with the producer about which
    of them need a settle day.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred so the ref cache is warm
    # inside the app context each case builds.
    # pylint: disable=import-outside-toplevel
    from app.utils.balance_predicates import settled_status_ids
    return settled_status_ids()


@contextmanager
def _statements_issued():
    """Record every SQL statement the engine executes inside the block.

    At the Engine level rather than a session's, so it sees what the PROCESS
    issued whichever session issued it -- the same probe
    ``test_loan_payment_service`` uses.  It cannot see ``BEGIN`` / ``COMMIT`` /
    ``ROLLBACK``, which psycopg2 issues through the connection, so the
    assertions below name TABLES rather than counting a total.

    Yields:
        The list of normalised SQL strings, appended to as the block runs.
    """
    seen = []

    def _record(conn, cursor, statement, params, context, executemany):  # noqa: ARG001
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


#: The amount model's PER-ROW chain, named by the table it reads rather than by
#: the option that asks for it, so the control grades what reached the DATABASE
#: and not what a caller passed.
#:
#: **The count matters, not the presence, and a mutation measured that.**  A
#: LAZY load reads the same table -- once per row -- so "some statement named
#: it" passes over a caller that states no options at all: spliced that way, an
#: earlier draft of the priced-feed control below stayed GREEN.  An eager
#: ``selectinload`` issues ONE query for the whole set, so the fixtures build
#: FOUR shadows and the assertions count.
#:
#: ``budget.transfers`` alone, and the three tables past it
#: (``transfer_templates``, ``template_amount_versions``,
#: ``loan_payment_settings``) are deliberately NOT asserted: a chained
#: ``selectinload`` issues no query when the step before it found nothing, and
#: these fixtures build AD-HOC transfers, whose ``transfer_template_id`` is
#: NULL -- measured, after a first draft of this control asserted all four and
#: failed on its own fixture.  A control that names a table its fixture cannot
#: reach grades the fixture.  ``Transaction.transfer`` is also the one
#: ``pricing_load_options`` calls a TRUE N+1 (one query per shadow), so it is
#: the right table to hold the seam on.
_PRICING_TABLE = "budget.transfers"


class TestTheCallerStatesItsOwnEagerLoad:
    """A load that costs a round trip is the CONSUMER's declaration, not the loader's.

    Plan step **balance:X-bl-2a**.  ``query_shadow_income`` baked
    ``pricing_load_options()`` into itself, so a ROW loader decided what its
    callers would traverse -- and a producer cannot know that.  Three of its six
    consumers never touched the chain they paid five statements for; finding
    **N-296** is the same defect inverted, seven batch callers reaching a loader
    without it and paying a query per definition.

    **The pair below is DISJOINT on purpose.**  One case asserts the date feed
    reads no pricing table, the other that the priced feed reads them all.  A
    single case in either direction would pass over the wrong fix: dropping the
    options everywhere satisfies the first, and restoring them to the loader
    satisfies the second.
    """

    def test_the_date_feed_reads_no_pricing_table(
        self, app, db, seed_user, seed_periods,
    ):
        """``payment_installments(options=())`` touches no amount-model table.

        The whole point of the seam: the schedule replay's reference reads three
        dates per payment and no figure, so it must not pay for -- or be able to
        fail on -- the tier that prices one.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            _seam_shadows(seed_user, loan, seed_periods)
            db.session.expire_all()

            with _statements_issued() as statements:
                installments = payment_installments(
                    loan.id, seed_user["scenario"].id, _PAYMENT_DAY, options=(),
                )

            assert len(installments) == _SEAM_SHADOWS, (
                "the feed came back short -- a control over what a load reads "
                "must first have loaded something"
            )
            reads = _reads_of(_PRICING_TABLE, statements)
            assert reads == 0, (
                f"the date feed issued {reads} statement(s) against "
                f"{_PRICING_TABLE}: the amount model's eager load has moved "
                f"back into the loader, so a consumer of the DATES is paying "
                f"for the tier that prices them"
            )

    def test_the_priced_feed_reads_every_pricing_table(
        self, app, db, seed_user, seed_periods,
    ):
        """``get_payment_history`` states the chain, so no row walks per-row.

        The other direction, and it is not the same assertion negated: this is
        what fails if the options are simply deleted rather than moved to the
        caller, which would turn every priced row into a walk to its parent.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            _seam_shadows(seed_user, loan, seed_periods)
            db.session.expire_all()
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            with _statements_issued() as statements:
                payments = get_payment_history(loan.id, basis, _PAYMENT_DAY)

            assert len(payments) == _SEAM_SHADOWS, "the priced feed came back short"
            reads = _reads_of(_PRICING_TABLE, statements)
            assert reads == 1, (
                f"the priced feed issued {reads} statements against "
                f"{_PRICING_TABLE} over {_SEAM_SHADOWS} shadows, not the ONE "
                f"an eager load takes: a count of {_SEAM_SHADOWS} is the "
                f"per-row walk finding N-296 is about, and a count of 0 means "
                f"nothing was loaded at all"
            )


class TestThePartitionIsTotal:
    """A shadow the partition cannot place RAISES rather than vanishing."""

    def test_an_unplaceable_status_is_refused(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A settled row stops being settled: the producer refuses it by name.

        The arm that keeps "settled or Projected, nothing else" from being a
        claim nobody checks.  Constructing a sixth ``ref.statuses`` row would
        mutate a process-wide cache other tests share, so the SEED is left alone
        and the producer's own predicate is narrowed instead -- which puts a real
        row in exactly the state a sixth status would: neither settled nor
        Projected.  The alternative to raising is that the row falls out of the
        loan's schedule, its balance and its plan, silently.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.DONE,
                settled_on=seed_periods[1].start_date,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            # It places the row before the predicate is narrowed, so the refusal
            # below is the narrowing and not the fixture.
            assert _shadows.income_shadows(
                loan.id, scenario_id, options=(),
            ).settled

            monkeypatch.setattr(_shadows, "settled_status_ids", frozenset)

            with pytest.raises(ValueError, match="neither settled nor Projected"):
                _shadows.income_shadows(loan.id, scenario_id, options=())
