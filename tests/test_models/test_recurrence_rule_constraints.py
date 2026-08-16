"""Database CHECK constraint regression tests for budget.recurrence_rules.

Locks the STORAGE tier: what the table itself refuses, with no service, no
schema and no form in front of it.  Two families live here.

**The legacy anchor columns**, which still carry their ranges: ``day_of_month``
and ``month_of_year`` fall in 1..31 and 1..12, and ``due_day_of_month`` does the
same when populated.  Materialised by:

  * ck_recurrence_rules_due_dom -- migration f15a72a3da6c
  * ck_recurrence_rules_dom -- migration 1702cadcae54 (H-3 fix)
  * ck_recurrence_rules_moy -- migration 1702cadcae54 (H-3 fix)

Without them the recurrence engine would translate values like day_of_month=99
into impossible calendar dates, silently generating transactions on dates that
do not exist and corrupting balance projections downstream.  **Nothing the
application writes can produce such a row since plan step R7c-b** -- those two
columns became a storage ENCODING the write door derives from the resolved
first occurrence -- so what these cases still cover is a restore, a hand edit,
or a caller that reaches the table without the door.  Plan step R7c-c drops the
columns and these cases with them.

**The two CHECKs plan step R7c-b added**, which is the other half of that
step: with the two-axis columns authored and ``NOT NULL``, the table can state
rules it previously could not, and each closes a state a service guard used to
stand in for.  A THIRD was drafted -- ``end_date >= starts_on`` -- and held
back on a developer ruling; :class:`TestTheWindowIsHeldAtTheDoorsAndNotTheTable`
carries the reason and pins the state it would have refused.

  * ck_recurrence_rules_nominal_day -- COMPLETED at R7c-b with the clamp
    equality, which is what let ``_occurrence._require_generable`` lose its
    third refusal;
  * ck_recurrence_rules_starts_on_range -- the first occurrence falls inside
    the calendar this application reaches.

Every rule here is built COLUMN BY COLUMN rather than through
``recurrence.author_rule``, and deliberately: the door refuses each of these
values before it writes, so authoring one would exercise the door and prove
nothing about the table.  :func:`_storable_columns` supplies the four ``NOT
NULL`` columns every row needs so that the INSERT reaches the CHECK under test
rather than dying on a null.

Audit reference: H-3 of
docs/audits/security-2026-04-15/model-migration-drift.md.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern

#: A first occurrence inside the calendar window, on a day every month holds.
#:
#: Day 15 so no case below is accidentally month-end clamped: the nominal-day
#: CHECK turns on whether ``starts_on`` IS its own month's last day, and a
#: fixture that happened to sit on one would make the range cases pass for the
#: wrong reason.
_A_FIRST_OCCURRENCE = date(2026, 1, 15)


def _monthly_pattern_id():
    """Return the ref.recurrence_patterns id for the Monthly pattern."""
    return (
        db.session.query(RecurrencePattern)
        .filter_by(name="Monthly")
        .one()
        .id
    )


def _annual_pattern_id():
    """Return the ref.recurrence_patterns id for the Annual pattern."""
    return (
        db.session.query(RecurrencePattern)
        .filter_by(name="Annual")
        .one()
        .id
    )


def _storable_columns(**overrides):
    """Return the columns a rule needs to REACH a CHECK, plus *overrides*.

    Plan step R7c-b made ``unit_id``, ``placement_id``, ``shift_id`` and
    ``starts_on`` ``NOT NULL``, so a row stating only a pattern now fails on a
    null before any CHECK is evaluated -- which would turn every case in this
    file green-for-the-wrong-reason if it were left to fail that way, since
    ``IntegrityError`` covers both.  Each case therefore starts from a storable
    row and poisons exactly the column it is about.

    An ordinary monthly cadence: the values name a real recurrence, so nothing
    here is itself the violation under test.

    Args:
        **overrides: Columns to set or replace.

    Returns:
        dict: The keyword arguments for a :class:`RecurrenceRule`.
    """
    columns = {
        "pattern_id": _monthly_pattern_id(),
        "unit_id": ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH),
        "placement_id": ref_cache.period_placement_id(
            PeriodPlacementEnum.CONTAINING_DATE,
        ),
        "shift_id": ref_cache.business_day_shift_id(
            BusinessDayShiftEnum.NONE,
        ),
        "starts_on": _A_FIRST_OCCURRENCE,
    }
    columns.update(overrides)
    return columns


def _refused(seed_user, constraint, label="", **columns):
    """Flush a rule built from *columns* and assert *constraint* refuses it.

    Args:
        seed_user: The ``seed_user`` fixture dict, whose user owns the rule.
        constraint: The CHECK constraint name the message must name.
        label: What to name in the failure, for a parametrized case.
        **columns: Passed to :func:`_storable_columns`.

    Raises:
        AssertionError: When the flush succeeds, or a DIFFERENT constraint
            refuses it -- which is what tells a "NOT NULL caught it first"
            failure apart from the refusal the case is about.
    """
    rule = RecurrenceRule(
        user_id=seed_user["user"].id, **_storable_columns(**columns),
    )
    db.session.add(rule)
    with pytest.raises(IntegrityError) as exc_info:
        db.session.flush()
    assert constraint in str(exc_info.value), label
    db.session.rollback()


class TestRecurrenceRuleRangeConstraints:
    """Out-of-range day/month values rejected at flush time."""

    def test_day_of_month_above_31_rejected(self, app, db, seed_user):
        """day_of_month=99 raises IntegrityError on insert.

        A future caller that allowed an out-of-range day to slip past
        the schema layer would otherwise corrupt the recurrence
        engine's date arithmetic.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_dom", day_of_month=99,
            )

    def test_day_of_month_zero_rejected(self, app, db, seed_user):
        """day_of_month=0 raises IntegrityError on insert.

        Zero would map to "the day before the 1st", which the engine
        would silently shift into the previous month.  Pinning the
        lower bound at 1 makes the rejection explicit.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_dom", day_of_month=0,
            )

    def test_due_day_of_month_above_31_rejected(self, app, db, seed_user):
        """due_day_of_month=99 raises IntegrityError on insert.

        Mirrors the day_of_month bound; this constraint already
        existed in production before the H-3 fix (added by migration
        f15a72a3da6c) and the test is here as a complementary backstop
        so all three recurrence-rule range checks are exercised in
        one file.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_due_dom",
                day_of_month=15, due_day_of_month=99,
            )

    def test_month_of_year_above_12_rejected(self, app, db, seed_user):
        """month_of_year=15 raises IntegrityError on insert.

        Without this constraint the annual recurrence pattern would
        treat month=15 as "December plus three months" thanks to
        Python's date-overflow arithmetic, generating transactions in
        a year the user did not specify.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_moy",
                pattern_id=_annual_pattern_id(),
                unit_id=ref_cache.recurrence_unit_id(RecurrenceUnitEnum.YEAR),
                month_of_year=15, day_of_month=1,
            )

    def test_month_of_year_zero_rejected(self, app, db, seed_user):
        """month_of_year=0 raises IntegrityError on insert.

        Zero would shift the annual recurrence into the previous
        December.  Pinning the lower bound at 1 makes the rejection
        explicit at the storage tier.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_moy",
                pattern_id=_annual_pattern_id(),
                unit_id=ref_cache.recurrence_unit_id(RecurrenceUnitEnum.YEAR),
                month_of_year=0, day_of_month=1,
            )

    def test_interval_n_and_offset_periods_default_non_null(
        self, app, db, seed_user,
    ):
        """A rule created without interval_n / offset_periods persists 1 / 0.

        Both columns are NOT NULL with a server_default (1 / 0) plus the
        model's Python ``default=``, so a rule constructed without setting
        them lands a real integer once persisted -- never NULL.  The
        recurrence engine (the PERIOD-unit occurrence walk), the
        obligations frequency label, and
        ``obligations_aggregator``'s monthly equivalent reads these directly,
        through ``recurrence.cadence_of`` --
        ``interval_n`` as a modulus / division divisor -- with NO
        ``or 1`` / ``or 0`` coalesce (deep-hunt #65), so this pins the
        invariant that makes that safe: a persisted rule can never feed
        them None.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id, **_storable_columns(),
            )
            db.session.add(rule)
            db.session.flush()
            assert rule.interval_n == 1
            assert rule.offset_periods == 0
            db.session.rollback()

    def test_null_day_and_month_allowed(self, app, db, seed_user):
        """A RecurrenceRule with day_of_month=NULL and month_of_year=NULL inserts.

        Patterns like 'every_n_periods' do not need either field.
        Asserts the CHECK predicates' NULL branches admit the common
        case so a future regression that tightens the predicates
        (drops the IS NULL branch) breaks here loudly instead of
        breaking the every-period pattern silently.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id, **_storable_columns(),
            )
            db.session.add(rule)
            db.session.flush()
            assert rule.id is not None
            assert rule.day_of_month is None
            assert rule.month_of_year is None
            db.session.rollback()


class TestTheNominalDayIsOnlyEverAClamp:
    """``ck_recurrence_rules_nominal_day``, completed at plan step R7c-b.

    ``nominal_day`` records the day a rule MEANS when its first occurrence's
    own month was too short to hold it (ruling R-R3), and NOTHING else.  Three
    conjuncts carry that, none implied by the others, and each has a case here
    because until R7c-b only two of them were on the table -- the third lived
    as a runtime guard in ``_occurrence._require_generable``, which that step
    deleted once the schema could say the whole rule.
    """

    def test_the_clamped_pair_is_admitted(self, app, db, seed_user):
        """The one shape the column exists for: April 30 meaning the 31st.

        The positive control.  Without it every refusal below would also pass
        against a CHECK that simply refused every non-NULL value, which would
        delete month-end recurrence from the application.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                **_storable_columns(
                    starts_on=date(2026, 4, 30), nominal_day=31,
                ),
            )
            db.session.add(rule)
            db.session.flush()

            assert rule.id is not None
            db.session.rollback()

    def test_a_day_the_month_could_hold_is_refused(self, app, db, seed_user):
        """(2026-04-15, 30): a nominal day beside a date that never clamped.

        **The conjunct plan step R7c-b added, and the reason it is worth a
        case.**  30 is in range and it does exceed the date's 15, so R7c-a's
        two-conjunct CHECK admitted this row -- and April HAS a 30th, so the
        rule would fire on a day ``starts_on`` does not name and no surface
        could say which of the two was meant.

        NEGATIVE CONTROL: drop the LEAST(...) conjunct from
        ``ck_recurrence_rules_nominal_day`` and this goes green.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_nominal_day",
                starts_on=date(2026, 4, 15), nominal_day=30,
            )

    def test_a_day_at_or_below_the_dates_own_is_refused(
        self, app, db, seed_user,
    ):
        """(2026-01-31, 31) restates the day the date already carries.

        January HAS a 31st, so this pair says one thing twice -- the two
        representations ruling R-R16 removes.  The ``>`` conjunct is what
        refuses it.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_nominal_day",
                starts_on=date(2026, 1, 31), nominal_day=31,
            )

    def test_a_day_below_29_is_refused(self, app, db, seed_user):
        """(2026-02-27, 28) names a day no month is ever too short to hold.

        The domain conjunct.  Every month holds its first 28 days, so a value
        below 29 can never be a clamp; the pair also fails the equality above,
        which is why the case names the constraint rather than the branch.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_nominal_day",
                starts_on=date(2026, 2, 27), nominal_day=28,
            )


class TestTheWindowIsHeldAtTheDoorsAndNotTheTable:
    """There is deliberately NO ``end_date >= starts_on`` CHECK (R7c-b).

    The ruling (developer, 2026-08-15) and the reason for it: these two columns
    hold two different KINDS of fact, and a constraint cannot tell them apart.
    A USER-authored stop before the start is a mistake to report; the window
    ``loan_recurrence_sync`` DERIVES for a loan payment is empty whenever the
    loan owes nothing yet, and that is a correct answer.  So the rule lives at
    the two authoring doors, which are the only layers that know which they are
    looking at, and this class pins the table's side of that division.
    """

    def test_an_end_before_the_start_is_ADMITTED_by_the_table(
        self, app, db, seed_user,
    ):
        """The state a CHECK here would have made unstorable.

        Measured: a loan originating 2026-08-01 with ``payment_day`` 1 has its
        first contractual installment on 2026-09-01, and truing its balance to
        zero on 2026-08-15 makes ``recurrence_end_date`` answer that day.  The
        window is empty and forward generation emits nothing, which is exactly
        right for a loan that owes nothing -- and a CHECK would have turned it
        into an unhandled ``CheckViolation`` out of the true-up.

        Asserted rather than left implicit because "no constraint" is easy to
        re-add by accident, and re-adding it re-opens a 500 on an ordinary
        money action.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                **_storable_columns(
                    starts_on=date(2026, 9, 1), end_date=date(2026, 8, 15),
                ),
            )
            db.session.add(rule)
            db.session.flush()

            assert rule.id is not None
            db.session.rollback()

    def test_an_end_ON_the_start_is_admitted(self, app, db, seed_user):
        """The boundary the DOORS use ``>=`` rather than ``>`` for.

        A real cadence -- a one-off whose closing bound is its own first
        occurrence, which fires exactly once.  Pinned here as well as at the
        doors because it is the case a tightening would break first.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                **_storable_columns(
                    starts_on=date(2026, 6, 1), end_date=date(2026, 6, 1),
                ),
            )
            db.session.add(rule)
            db.session.flush()

            assert rule.id is not None
            db.session.rollback()

    def test_no_end_at_all_is_admitted(self, app, db, seed_user):
        """The common case: a rule that never ends, which 41 of 46 live rules are.

        Pinned so a future tightening cannot make every unbounded recurrence
        unstorable without breaking here loudly.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                **_storable_columns(end_date=None),
            )
            db.session.add(rule)
            db.session.flush()

            assert rule.id is not None
            db.session.rollback()


class TestTheStartFallsInsideTheCalendar:
    """``ck_recurrence_rules_starts_on_range``, added at plan step R7c-b."""

    @pytest.mark.parametrize(
        "label,starts_on",
        [
            ("below the window", date(1999, 12, 31)),
            ("above the window", date(2101, 1, 1)),
        ],
    )
    def test_a_start_outside_the_window_is_refused(
        self, app, db, seed_user, label, starts_on,
    ):
        """2000-01-01..2100-12-31 is how far this application's calendar reaches.

        It backs a MEASURED 500 rather than tidiness: past the saved horizon
        the pay calendar projects the covering paycheck by adding
        ``cadence_days`` to a start, so a date near ``date.max`` raised
        ``OverflowError`` -- from outside the recurrence package's error
        hierarchy, so the preview endpoint's own handler did not catch it.

        Both edges, because a one-sided bound would leave the other half of
        the window open and the failure would look identical.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_starts_on_range",
                label=label, starts_on=starts_on,
            )

    @pytest.mark.parametrize(
        "label,starts_on",
        [
            ("the first day", date(2000, 1, 1)),
            ("the last day", date(2100, 12, 31)),
        ],
    )
    def test_the_windows_own_edges_are_admitted(
        self, app, db, seed_user, label, starts_on,
    ):
        """BETWEEN is inclusive, and both endpoints are real dates.

        The positive control for the case above: an off-by-one that excluded
        an endpoint would make the refusals pass while quietly narrowing the
        window the application says it reaches.
        """
        with app.app_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                **_storable_columns(starts_on=starts_on),
            )
            db.session.add(rule)
            db.session.flush()

            assert rule.id is not None, label
            db.session.rollback()
