"""The pay calendar's derivation sweep (plan steps C1 and C4-c).

**What this module was, and why the half that is gone had to go.**  Plan step
C1 said the derivation must be proven equal to what ``budget.pay_periods``
stores before anything read it, wrote it, or dropped the columns.  So this
module was a COMPARATOR: it drove ``derive_periods`` over an owner's paydays,
diffed the answer against the stored ``end_date`` / ``period_index`` row by
row, and was shared by two datasets -- a manual harness over a clone of
production and this suite over schedules the live data cannot supply.  Plan
step ``pay_calendar:C4-c`` dropped both columns, so that comparison has no
second side, and the comparator, its payday control and its verdict went with
it.  *The proof they were is in migration ``b7a41e2c9d63``'s docstring, taken
on production itself the day the columns went: 63 rows, 0 end mismatches, 0
index mismatches, 0 gaps, 0 overlaps.*

**What survives is the half that was never about the columns**: a catalogue of
schedule shapes with their derivations worked out BY HAND, so the sweep asserts
VALUES rather than agreeing with whatever the code produced.  Production's
schedule is perfectly regular -- one cadence, every period exactly 14 days --
so a run over it exercises ONE shape however many rows it has.  These are the
others.

:func:`cadence_control` survives with them, and it is the instrument that
measures the branch a regular schedule cannot: ``end_date`` has two sources --
the next payday for every period but the last, the cadence for the last -- and
on a contiguous fortnightly schedule the two agree everywhere.  Re-deriving at
a neighbouring cadence separates them: with the branch correct exactly one end
moves, by exactly one day; with it wrong every end moves; with it stubbed to
reuse a value, nothing does.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

# Imported from the package's PUBLIC surface, which is what every consumer
# depends on.  Reaching into ``pay_calendar._derive`` would test a path no
# application module is allowed to take (the W9910 gate).
from app.services.pay_calendar import (
    MAX_CADENCE_DAYS,
    DerivedPeriod,
    derive_periods,
)


@dataclass(frozen=True)
class CadenceControl:
    """What moved when the calendar was re-derived at a neighbouring cadence.

    Attributes:
        applicable: Whether the control could run at all.  ``False`` only for
            an empty payday set, where there is no end to move.
        probe_cadence: The cadence the schedule was re-derived at.
        expected_shift_days: How far the last end should move -- ``+1`` for a
            probe one day longer, ``-1`` when the real cadence is already at
            :data:`~app.services.pay_calendar.MAX_CADENCE_DAYS` and the probe
            has to go the other way.
        moved: ``(payday, days its derived end moved)`` for every row whose
            derived end changed, in ``start_date`` order.
    """

    applicable: bool
    probe_cadence: int
    expected_shift_days: int
    moved: "tuple[tuple[date, int], ...]"

    @property
    def fired(self) -> bool:
        """Whether the control produced exactly the movement it demands.

        Returns:
            ``True`` when exactly one row moved and it moved by
            :attr:`expected_shift_days`.  Anything else is a finding: no
            movement means the projection branch is not reached, and movement
            on more than one row means ends that should be dictated by the
            NEXT PAYDAY are being computed from the cadence instead -- the
            pre-normalization defect, which reproduces a regular schedule
            exactly and is invisible to every other check here.
        """
        return (
            self.applicable
            and len(self.moved) == 1
            and self.moved[0][1] == self.expected_shift_days
        )

    def as_blob(self) -> dict:
        """Return a JSON-stable mapping of the control's outcome.

        Returns:
            The control as plain JSON-serialisable types.
        """
        return {
            "applicable": self.applicable,
            "fired": self.fired,
            "probe_cadence": self.probe_cadence,
            "expected_shift_days": self.expected_shift_days,
            "moved": [
                {"start_date": payday.isoformat(), "shift_days": shift}
                for payday, shift in self.moved
            ],
        }


def cadence_control(
    paydays: "Sequence[tuple[int | None, date]]", cadence_days: int,
) -> CadenceControl:
    """Re-derive at a neighbouring cadence and report every end that moved.

    The instrument for the branch a regular schedule cannot show.  ``end_date``
    has two sources -- the next payday for every period but the last, the
    cadence for the last -- and on a contiguous fortnightly schedule the two
    AGREE on every row.  So a derivation that took the cadence branch
    everywhere reproduces such a schedule exactly.  Measured while plan step C1
    was reviewed: that mutant failed 16 suite tests and a byte-identity run over
    a production clone still exited 0.

    Changing the cadence by one day separates them: with the branch correct
    exactly one end moves, by exactly one day.  With the branch wrong every end
    moves.  With the branch stubbed to reuse a value, nothing moves.

    Args:
        paydays: The owner's complete payday set as ``(period_id, payday)``
            pairs, in any order.
        cadence_days: The real cadence, already validated by the caller's own
            :func:`~app.services.pay_calendar.derive_periods` call.

    Returns:
        The :class:`CadenceControl`.  ``applicable`` is ``False`` for an empty
        payday set, where there is no end to move.
    """
    probe = (
        cadence_days - 1 if cadence_days >= MAX_CADENCE_DAYS
        else cadence_days + 1
    )
    baseline = derive_periods(paydays, cadence_days)
    probed = derive_periods(paydays, probe)
    return CadenceControl(
        applicable=bool(baseline),
        probe_cadence=probe,
        expected_shift_days=probe - cadence_days,
        moved=tuple(
            (before.start_date, (after.end_date - before.end_date).days)
            for before, after in zip(baseline, probed)
            if after.end_date != before.end_date
        ),
    )


@dataclass(frozen=True)
class IrregularShape:
    """One schedule the live data cannot supply, with its hand-computed answer.

    The generated sweep plan step C1 asks for.  Production is contiguous, every
    period is exactly 14 days long and there is exactly one cadence, so a run
    over it exercises ONE shape however many rows it has.  These are the
    others -- each a payday set with the derived values worked out by hand, so
    the sweep asserts VALUES rather than agreeing with whatever the code
    produced.

    Attributes:
        label: Short identifier, used as the test id.
        why: What this shape exists to pin, in one sentence.
        cadence_days: The cadence the derivation is driven with.
        paydays: ``(period_id, payday)`` per row, **deliberately not sorted**:
            every use of a shape therefore also exercises the derivation's own
            ordering, and ``index_out_of_date_order`` is the shape that pins
            it.  The ids are written out rather than generated so that shape
            can pin a row's IDENTITY following its payday through the sort.
        expected: The hand-computed derivation, in ``start_date`` order.

    *Two fields went at plan step ``pay_calendar:C4-c``: the four-column
    ``stored`` tuple that stood in for a ``budget.pay_periods`` row, and
    ``disagreeing_starts``, which named the paydays whose stored columns the
    derivation contradicted.  Neither has a subject once nothing is stored.
    Four shapes were BUILT around such a disagreement -- the hole, the shared
    boundary day, the out-of-order ordinal and the outgrown cadence -- and all
    four survive as payday SETS, because the payday set was always the real
    input and the stored row was only what a table would have held beside it.*
    """

    label: str
    why: str
    cadence_days: int
    paydays: "tuple[tuple[int, date], ...]"
    expected: "tuple[DerivedPeriod, ...]"


def shape(label: str) -> IrregularShape:
    """Return the :data:`IRREGULAR_SHAPES` entry named *label*.

    Args:
        label: The shape's :attr:`IrregularShape.label`.

    Returns:
        The matching shape.

    Raises:
        KeyError: No shape carries that label -- a renamed shape must not
            silently drop the test that used it.
    """
    for candidate in IRREGULAR_SHAPES:
        if candidate.label == label:
            return candidate
    raise KeyError(
        f"no IrregularShape labelled {label!r}; the catalogue holds "
        f"{[candidate.label for candidate in IRREGULAR_SHAPES]}"
    )


#: The generated sweep.  Coverage is STATED rather than implied (the plan's
#: "no silent caps" rule): every branch the derivation has -- the ``lead`` end,
#: the projected end, the empty schedule -- against every schedule irregularity
#: the arc has named.
IRREGULAR_SHAPES: "tuple[IrregularShape, ...]" = (
    IrregularShape(
        label="empty",
        why=(
            "A user who has never generated a schedule, and the companion "
            "role, which by design holds no paydays of its own -- measured on "
            "production 2026-08-08, user 2 has zero."
        ),
        cadence_days=14,
        paydays=(),
        expected=(),
    ),
    IrregularShape(
        label="single_payday",
        why=(
            "The one schedule where EVERY end is projected, and the state "
            "registration leaves a new owner in (auth_service writes one "
            "bootstrap payday)."
        ),
        cadence_days=14,
        # 2026-03-26 + 13 days = 2026-04-08.  Production's own first payday.
        paydays=((1, date(2026, 3, 26)),),
        expected=(
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 3, 26),
                end_date=date(2026, 4, 8),
                end_is_projected=True,
            ),
        ),
    ),
    IrregularShape(
        label="biweekly_five",
        why=(
            "The ordinary schedule, and the baseline every control is "
            "measured against: five contiguous fortnights, which is what the "
            "writer produces and what production is 63 of."
        ),
        cadence_days=14,
        paydays=(
            (1, date(2026, 1, 2)),
            (2, date(2026, 1, 16)),
            (3, date(2026, 1, 30)),
            (4, date(2026, 2, 13)),
            (5, date(2026, 2, 27)),
        ),
        expected=(
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 15),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=2,
                period_index=1,
                start_date=date(2026, 1, 16),
                end_date=date(2026, 1, 29),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=3,
                period_index=2,
                start_date=date(2026, 1, 30),
                end_date=date(2026, 2, 12),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=4,
                period_index=3,
                start_date=date(2026, 2, 13),
                end_date=date(2026, 2, 26),
                end_is_projected=False,
            ),
            # projected: 2026-02-27 + 13 = 2026-03-12 (February 2026 has 28
            # days, so the span crosses into March).
            DerivedPeriod(
                period_id=5,
                period_index=4,
                start_date=date(2026, 2, 27),
                end_date=date(2026, 3, 12),
                end_is_projected=True,
            ),
        ),
    ),
    IrregularShape(
        label="leap_day_span",
        why=(
            "February 29 inside a period's span rather than argued about -- "
            "2028 is the next leap year the live schedule reaches."
        ),
        cadence_days=14,
        # 2028-02-18 + 13 days crosses Feb 29 -> 2028-03-02.
        # 2028-03-03 + 13 days = 2028-03-16.
        paydays=(
            (1, date(2028, 2, 18)),
            (2, date(2028, 3, 3)),
        ),
        expected=(
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2028, 2, 18),
                end_date=date(2028, 3, 2),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=2,
                period_index=1,
                start_date=date(2028, 3, 3),
                end_date=date(2028, 3, 16),
                end_is_projected=True,
            ),
        ),
    ),
    IrregularShape(
        label="one_day_periods",
        why=(
            "Two paydays a day apart are two facts and the period between "
            "them is one day long -- which ck_pay_periods_date_order "
            "(start_date < end_date) made illegal to STORE, plan finding P9.  "
            "The derivation always produced it without complaint; the "
            "constraint was the artifact, and plan step pay_calendar:C4-c "
            "dropped it."
        ),
        cadence_days=1,
        paydays=(
            (1, date(2026, 1, 1)),
            (2, date(2026, 1, 2)),
            (3, date(2026, 1, 3)),
        ),
        expected=(
            # lead - 1: 2026-01-02 - 1 = 2026-01-01.
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 1),
                end_is_projected=False,
            ),
            # lead - 1: 2026-01-03 - 1 = 2026-01-02.
            DerivedPeriod(
                period_id=2,
                period_index=1,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 2),
                end_is_projected=False,
            ),
            # projected: 2026-01-03 + (1 - 1) = 2026-01-03.
            DerivedPeriod(
                period_id=3,
                period_index=2,
                start_date=date(2026, 1, 3),
                end_date=date(2026, 1, 3),
                end_is_projected=True,
            ),
        ),
    ),
    IrregularShape(
        label="ninety_day_cadence",
        why=(
            "cadence_days is user-selectable 1..365 "
            "(ck_pay_schedule_cadence_range), so a quarterly schedule is "
            "reachable configuration; it is also the cadence the recurrence "
            "arc's own baseline uses for its long-cadence shapes."
        ),
        cadence_days=90,
        # 2026-01-01 + 89 = 2026-03-31; 2026-04-01 + 89 = 2026-06-29;
        # 2026-06-30 + 89 = 2026-09-27.  2026 is not a leap year.
        paydays=(
            (1, date(2026, 1, 1)),
            (2, date(2026, 4, 1)),
            (3, date(2026, 6, 30)),
        ),
        expected=(
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 3, 31),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=2,
                period_index=1,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 6, 29),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=3,
                period_index=2,
                start_date=date(2026, 6, 30),
                end_date=date(2026, 9, 27),
                end_is_projected=True,
            ),
        ),
    ),
    IrregularShape(
        label="payday_jump",
        why=(
            "Plan finding P2's shape, as the payday set it always was: a "
            "fortnight and then a 28-day step, which the write door accepts.  "
            "The days between two paydays belong to the EARLIER one, so the "
            "middle period runs 28 days -- where the stored end_date C4-c "
            "dropped could say 14 and leave the other fourteen funded by no "
            "paycheck at all."
        ),
        cadence_days=14,
        paydays=(
            (1, date(2026, 1, 2)),
            (2, date(2026, 1, 16)),
            (3, date(2026, 2, 13)),
        ),
        expected=(
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 15),
                end_is_projected=False,
            ),
            # lead - 1 = 2026-02-13 - 1 = 2026-02-12: the fourteen days a
            # stored column could have failed to cover join the period before
            # them.
            DerivedPeriod(
                period_id=2,
                period_index=1,
                start_date=date(2026, 1, 16),
                end_date=date(2026, 2, 12),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=3,
                period_index=2,
                start_date=date(2026, 2, 13),
                end_date=date(2026, 2, 26),
                end_is_projected=True,
            ),
        ),
    ),
    IrregularShape(
        label="thirteen_day_period",
        why=(
            "Plan finding P4's shape as a payday set: two paydays thirteen "
            "days apart, one short of the cadence.  Stored, this was the pair "
            "whose spans SHARED a boundary day -- integrity_check BA-04's "
            "predicate was p2.start_date < p1.end_date, so a day covered "
            "twice passed the weekly check.  Derived, the first period ends "
            "the day before the second opens and there is no shared day to "
            "miss."
        ),
        cadence_days=14,
        paydays=(
            (1, date(2026, 1, 2)),
            (2, date(2026, 1, 15)),
        ),
        expected=(
            # lead - 1 = 2026-01-15 - 1 = 2026-01-14.
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 14),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=2,
                period_index=1,
                start_date=date(2026, 1, 15),
                end_date=date(2026, 1, 28),
                end_is_projected=True,
            ),
        ),
    ),
    IrregularShape(
        label="index_out_of_date_order",
        why=(
            "The paydays arrive in the WRONG ORDER, which pins that the "
            "derivation sorts rather than trusting its caller.  Stored, an "
            "ordinal disagreeing with payday order was a state NEITHER table "
            "constraint kept out -- uq_pay_periods_user_index refused a "
            "DUPLICATE ordinal and ck_pay_periods_positive_index a NEGATIVE "
            "one, and 0 and 1 are neither -- and plan step pay_calendar:C4-c "
            "dropped all three.  Derived, the ordinal IS the payday order, "
            "and the row IDS prove the sort moved the rows: they run 2 then 1 "
            "down the derived calendar."
        ),
        cadence_days=14,
        paydays=(
            (1, date(2026, 1, 30)),
            (2, date(2026, 1, 2)),
        ),
        expected=(
            # 2026-01-02 sorts first, so it is index 0 -- carrying id 2, the
            # id declared second.  Its end runs to the day before the next
            # payday: 2026-01-30 - 1 = 2026-01-29.
            DerivedPeriod(
                period_id=2,
                period_index=0,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 29),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=1,
                period_index=1,
                start_date=date(2026, 1, 30),
                end_date=date(2026, 2, 12),
                end_is_projected=True,
            ),
        ),
    ),
    IrregularShape(
        label="cadence_changed_mid_schedule",
        why=(
            "regenerate_pay_periods keeps a head at the old cadence and "
            "rebuilds the tail at a new one, so a live schedule can hold "
            "paydays at two spacings.  Only the LAST end reads cadence_days, "
            "so the fortnightly head is untouched by the weekly cadence."
        ),
        cadence_days=7,
        paydays=(
            (1, date(2026, 1, 2)),
            (2, date(2026, 1, 16)),
            (3, date(2026, 1, 30)),
            (4, date(2026, 2, 6)),
        ),
        expected=(
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 15),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=2,
                period_index=1,
                start_date=date(2026, 1, 16),
                end_date=date(2026, 1, 29),
                end_is_projected=False,
            ),
            DerivedPeriod(
                period_id=3,
                period_index=2,
                start_date=date(2026, 1, 30),
                end_date=date(2026, 2, 5),
                end_is_projected=False,
            ),
            # projected at the NEW cadence: 2026-02-06 + 6 = 2026-02-12.
            DerivedPeriod(
                period_id=4,
                period_index=3,
                start_date=date(2026, 2, 6),
                end_date=date(2026, 2, 12),
                end_is_projected=True,
            ),
        ),
    ),
    IrregularShape(
        label="cadence_shorter_than_the_payday_spacing",
        why=(
            "The paydays are a fortnight apart and the cadence says a week, "
            "so the HORIZON is seven days shorter than the rhythm that "
            "produced the schedule -- and the derivation moves EXACTLY the "
            "one end DerivedPeriod.end_is_projected marks.  Plan finding P12 "
            "was the door that reached this state (a generate naming an "
            "existing payday created zero rows and still upserted the "
            "cadence); plan step C3-b closed that door, and an operator "
            "changing the setting still reaches the state."
        ),
        cadence_days=7,
        paydays=(
            (1, date(2026, 1, 2)),
            (2, date(2026, 1, 16)),
        ),
        expected=(
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 15),
                end_is_projected=False,
            ),
            # projected at the SHORTER cadence: 2026-01-16 + 6 = 2026-01-22,
            # seven days short of where a fortnightly horizon would reach.
            DerivedPeriod(
                period_id=2,
                period_index=1,
                start_date=date(2026, 1, 16),
                end_date=date(2026, 1, 22),
                end_is_projected=True,
            ),
        ),
    ),
)
