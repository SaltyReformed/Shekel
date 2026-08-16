"""The READ door: one composition, two named halves (plan step R7a).

``app.services.recurrence._reading`` is where a stored rule is turned into
what it MEANS and where its rows land.  Until plan step R7a it offered only
the second half (``rule_occurrences``), and a caller needing both -- the
Recurring surface, which words each definition's cadence AND dates its next
occurrence -- would have had to perform the resolve-then-place sequence
itself.  A second copy of that sequence is what this file exists to prevent.

What it holds:

* ``read_rule`` is THE composition, and ``rule_occurrences`` is its
  ``.placements`` projection rather than a parallel implementation;
* ``resolved_recurrence`` is the first step ALONE -- it walks no occurrence,
  so a caller that wants only the meaning computes nothing it discards;
* the empty-schedule answer lives in ONE place, and the four refusals
  ``resolve`` makes about the RULE are not swallowed with it.

A ``ref_cache`` id is needed to name a pattern, so these take the ``app``
fixture; the rules themselves are transient (never added to a session) and
every schedule is hand-built, so nothing here touches a pay-period table.
"""

from datetime import date

import pytest

from app import ref_cache
from app.enums import BusinessDayShiftEnum, RecurrencePatternEnum
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import (
    RecurrenceResolutionError,
    decode_pattern,
    read_rule,
    resolved_recurrence,
    rule_occurrences,
)
# Imported as a MODULE so a firing control patches the name the composition
# resolves at CALL time; patching this file's import would prove only that the
# harness reads what it reads.
from app.services.recurrence import _reading
from tests.test_services.test_recurrence_resolution import build_calendar

_USER_ID = 1

#: The first occurrence :func:`_rule` states unless a test names another.
#:
#: A date the shared :func:`build_calendar` schedule reaches, and a MONTHLY
#: rule's own first occurrence rather than a bound it is filtered against
#: (ruling R-R16) -- so every placement below is dated from it directly.
_A_FIRST_OCCURRENCE = date(2026, 4, 22)


def _rule(pattern_enum, starts_on=_A_FIRST_OCCURRENCE, **columns):
    """Return an unsaved rule naming *pattern_enum*, as R7c-b stores one.

    Transient by design: the read door takes a rule row and issues no query,
    so nothing here needs the rule to exist in a table.

    **It states the two-axis columns rather than a pattern alone** (plan step
    R7c-b).  ``unit_id`` and ``placement_id`` are what
    :func:`~app.services.recurrence.recurrence_spec` reads the cadence's shape
    off -- only the INTERVAL still comes from the pattern -- and all four are
    ``NOT NULL``, so a pattern-only row is a shape the table cannot hold.
    They are DECODED from the pattern rather than tabulated here, so a rule
    built for this file carries exactly what the write door would have written
    for the same name.

    Args:
        pattern_enum: The pattern the rule names.
        starts_on: The rule's first occurrence.  Defaults to
            :data:`_A_FIRST_OCCURRENCE`, which the shared
            :func:`build_calendar` schedule reaches.
        **columns: Any other column to override.

    Returns:
        The unsaved :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    # Decoded at interval 1 whatever the caller states, because the two axes
    # this reads are properties of the PATTERN alone -- and one case below
    # deliberately stores the interval ``ck_recurrence_rules_positive_interval``
    # refuses, which the decode would refuse first.
    reading = decode_pattern(ref_cache.recurrence_pattern_id(pattern_enum), 1)
    defaults = {
        "user_id": _USER_ID,
        "pattern_id": ref_cache.recurrence_pattern_id(pattern_enum),
        "interval_n": 1,
        "offset_periods": 0,
        "unit_id": ref_cache.recurrence_unit_id(reading.cadence.unit),
        "placement_id": ref_cache.period_placement_id(reading.placement),
        "shift_id": ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE),
        "starts_on": starts_on,
    }
    defaults.update(columns)
    return RecurrenceRule(**defaults)


class TestOneComposition:
    """``rule_occurrences`` is a projection, not a second implementation."""

    def test_read_rule_returns_both_halves(self, app):
        """The meaning and the placements, from one call."""
        with app.app_context():
            calendar = build_calendar()
            rule = _rule(RecurrencePatternEnum.MONTHLY)

            reading = read_rule(rule, calendar)

            assert reading.resolved is not None
            assert reading.resolved.day_of_month == 22
            assert reading.placements
            assert reading.placements[0].occurrence == date(2026, 4, 22)

    def test_rule_occurrences_equals_read_rules_placements(self, app):
        """The two answers agree, for every pattern the application models.

        Equality alone would also hold if ``rule_occurrences`` walked the
        cadence a second time and happened to agree, which is why the call
        count below is the control that proves it is a PROJECTION; this one
        covers the whole modelled vocabulary rather than one shape.
        """
        with app.app_context():
            calendar = build_calendar()

            for pattern in RecurrencePatternEnum:
                rule = _rule(pattern, starts_on=date(2026, 6, 15))

                assert rule_occurrences(rule, calendar) == (
                    read_rule(rule, calendar).placements
                )

    def test_rule_occurrences_walks_the_cadence_exactly_once(
        self, app, monkeypatch,
    ):
        """One call, one placement walk.

        Guards the projection from being re-implemented as its own
        resolve-then-place pair, which would double the work of the three
        surfaces that take it.
        """
        with app.app_context():
            calendar = build_calendar()
            rule = _rule(RecurrencePatternEnum.MONTHLY)

            calls = []
            real = _reading.occurrence_placements

            def counting(resolved, cal, **kwargs):
                calls.append(resolved)
                return real(resolved, cal, **kwargs)

            monkeypatch.setattr(_reading, "occurrence_placements", counting)
            placements = rule_occurrences(rule, calendar)

            # Shown to FIRE: an empty ``calls`` would make "exactly one" pass
            # for the wrong reason.
            assert placements
            assert len(calls) == 1


class TestTheMeaningAlone:
    """``resolved_recurrence`` answers the first step and stops."""

    def test_it_resolves_without_walking_an_occurrence(self, app, monkeypatch):
        """Nothing is computed and discarded.

        The archived drawer wants the cadence and not where rows land; making
        it pay for a placement walk it never renders is the "computed and
        thrown away" defect ledger row D26 names, one surface over.
        """
        with app.app_context():
            calendar = build_calendar()
            rule = _rule(RecurrencePatternEnum.MONTHLY)

            def fail_if_called(*_args, **_kwargs):
                raise AssertionError("the meaning-only read placed occurrences")

            monkeypatch.setattr(
                _reading, "occurrence_placements", fail_if_called,
            )
            resolved = resolved_recurrence(rule, calendar)

            assert resolved is not None
            assert resolved.starts_on == date(2026, 4, 22)

    def test_it_agrees_with_read_rules_own_half(self, app):
        """The two entry points cannot state different meanings."""
        with app.app_context():
            calendar = build_calendar()

            for pattern in RecurrencePatternEnum:
                rule = _rule(pattern, starts_on=date(2026, 6, 15))

                assert resolved_recurrence(rule, calendar) == (
                    read_rule(rule, calendar).resolved
                )


class TestTheEmptySchedule:
    """The one refusal answered rather than raised, held in one place."""

    def test_the_meaning_is_none(self, app):
        """An owner with no pay periods has no schedule to anchor against.

        ``resolve`` refuses it -- rightly, since registration bootstraps a
        period -- but the Recurring surface renders every definition a user
        has, and a 500 for a state no rule is wrong about would be a fence.
        """
        with app.app_context():
            empty = PayCalendar.from_paydays(
                paydays=(), cadence_days=None, user_id=_USER_ID,
            )
            rule = _rule(RecurrencePatternEnum.MONTHLY)

            assert resolved_recurrence(rule, empty) is None

    def test_read_rule_answers_both_halves_empty(self, app):
        """No meaning and no placements, never a meaning without placements."""
        with app.app_context():
            empty = PayCalendar.from_paydays(
                paydays=(), cadence_days=None, user_id=_USER_ID,
            )
            rule = _rule(RecurrencePatternEnum.EVERY_PERIOD)

            reading = read_rule(rule, empty)

            assert reading.resolved is None
            assert reading.placements == ()

    def test_the_projection_answers_empty_too(self, app):
        """The shape three surfaces and the baseline take is unchanged."""
        with app.app_context():
            empty = PayCalendar.from_paydays(
                paydays=(), cadence_days=None, user_id=_USER_ID,
            )
            rule = _rule(RecurrencePatternEnum.EVERY_PERIOD)

            assert rule_occurrences(rule, empty) == ()


class TestItSwallowsNothingElse:
    """The other refusals are about the RULE and must stay loud."""

    def test_an_unmodelled_pattern_still_raises(self, app):
        """A rule whose cadence cannot be derived is a broken invariant.

        The guard above is one condition, not a short-circuit before the call
        -- if it were the latter, every refusal would become an empty answer
        and a rule with no derivable cadence would render as one that never
        fires.
        """
        with app.app_context():
            calendar = build_calendar()
            highest = max(
                ref_cache.recurrence_pattern_id(member)
                for member in RecurrencePatternEnum
            )
            rule = _rule(RecurrencePatternEnum.MONTHLY)
            rule.pattern_id = highest + 1000

            with pytest.raises(RecurrenceResolutionError, match="matches no"):
                resolved_recurrence(rule, calendar)

            with pytest.raises(RecurrenceResolutionError, match="matches no"):
                read_rule(rule, calendar)

            with pytest.raises(RecurrenceResolutionError, match="matches no"):
                rule_occurrences(rule, calendar)

    def test_another_owners_schedule_still_raises(self, app):
        """An anchor measured against the wrong schedule is plausibly WRONG.

        Not an error the caller can see in the answer, which is why it is
        refused rather than reported.
        """
        with app.app_context():
            other = build_calendar(user_id=_USER_ID + 1)
            rule = _rule(RecurrencePatternEnum.MONTHLY)

            with pytest.raises(RecurrenceResolutionError, match="cannot be"):
                resolved_recurrence(rule, other)

    def test_a_non_positive_interval_still_raises(self, app):
        """``ck_recurrence_rules_positive_interval``'s reader-side refusal."""
        with app.app_context():
            calendar = build_calendar()
            rule = _rule(RecurrencePatternEnum.EVERY_N_PERIODS, interval_n=0)

            with pytest.raises(RecurrenceResolutionError, match="positive"):
                resolved_recurrence(rule, calendar)

    def test_a_day_outside_its_column_domain_still_raises(self, app):
        """A day of 99 would CLAMP to a month's last day, answering a lie.

        The column this asks about MOVED at plan step R7c-b and the refusal
        did not: ``day_of_month`` stopped being authored -- the write door
        encodes it from the resolved first occurrence -- so the authored day
        left with a domain of its own is ``due_day_of_month``, whose
        ``ck_recurrence_rules_due_dom`` this mirrors.
        """
        with app.app_context():
            calendar = build_calendar()
            rule = _rule(RecurrencePatternEnum.MONTHLY, due_day_of_month=99)

            with pytest.raises(
                RecurrenceResolutionError, match="due_day_of_month",
            ):
                resolved_recurrence(rule, calendar)

    def test_a_start_outside_the_calendar_window_still_raises(self, app):
        """A first occurrence past 2100 overflows the calendar's projection.

        The fourth rule-level refusal, and the one plan step R7c-b added: past
        the saved horizon the pay calendar projects the covering paycheck by
        adding ``cadence_days`` to a start, which raises ``OverflowError`` from
        outside this package's hierarchy.  It must reach the caller rather than
        being answered ``None`` beside the empty-schedule case -- the rule is
        wrong, not the schedule.
        """
        with app.app_context():
            calendar = build_calendar()
            rule = _rule(
                RecurrencePatternEnum.MONTHLY, starts_on=date(9999, 12, 31),
            )

            with pytest.raises(RecurrenceResolutionError, match="starts_on"):
                resolved_recurrence(rule, calendar)
