"""The pay calendar's derivation oracle (plan step C1).

``docs/plans/implementation_plan_pay_calendar.md`` step C1 says the derivation
must be proven equal to what ``budget.pay_periods`` stores before anything
reads it, writes it, or drops the columns.  This module is the instrument that
proof is taken with, and it is shared deliberately:

* ``tests/manual/verify_pay_calendar_derivation.py`` drives it over a CLONE of
  production -- the real 61 rows, the real cadence -- and writes a JSON blob;
* ``tests/test_services/test_pay_calendar_derivation.py`` drives it in the
  SUITE, over schedules the live data cannot supply and over schedules the real
  writer produces.

One comparator, two datasets.  Two comparators would be two producers that a
reader has to keep in step -- the shape the balance arc's verification standard
refuses ("never two producers that share code proving each other" is its
sibling rule; this is its converse, one producer read twice).  The verdict
logic lives here for a sharper reason: it is what decides whether a run PASSED,
it had two defects when it lived in the script, and a script outside pytest's
collection cannot be tested.  Everything in this module is exercised by the
suite.

**What it answers, and what it does not.**  It answers "does the derivation
reproduce the stored columns, row by row".  It does NOT answer "is the stored
column right" -- on a schedule with a hole or an overlap the two disagree BY
DESIGN, because the derivation cannot express either state, and the oracle's
job there is to name exactly which rows moved rather than to pass or fail.
Plan steps C3 and C4 read it the other way round: on a schedule with neither
defect, a row that moves is a regression.

**Rows are matched on ``start_date``, never on the stored index.**  The payday
is the fact and the table's own key (``uq_pay_periods_user_start``); the stored
index is one of the two values under test, so matching on it would assume the
answer.  Both sides are sorted by payday and walked in step, and the match is
CHECKED rather than trusted -- which is what lets
:attr:`RowComparison.stored_index` and :attr:`RowComparison.derived_index`
disagree and be SEEN to disagree.  Shape ``index_out_of_date_order`` in
:data:`IRREGULAR_SHAPES` is exactly that case, and it also proves the row id
survives the reordering.

**Two controls, because byte-identity over the live rows is blind twice.**
Production's schedule is perfectly regular, so ``lead(start) - 1`` and
``start + cadence - 1`` give the SAME answer on all 61 rows.  Measured during
this step's review: a derivation that used the projected form for EVERY period
-- the exact defect the normalization exists to remove -- reproduces the clone
byte-identically and exits 0.  :func:`perturb` moves a payday, which catches a
comparator that cannot see an index or an end change;
:func:`cadence_control` re-derives at a neighbouring cadence and requires
exactly ONE row -- the last -- to move by exactly one day, which catches the
branch confusion the payday control cannot.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from app.models.pay_period import PayPeriod
# Imported from the package's PUBLIC surface, which is what every consumer
# after plan step C2 will depend on.  Reaching into ``pay_calendar._derive``
# would test a path no application module is allowed to take (the W9910 gate).
from app.services.pay_calendar import (
    MAX_CADENCE_DAYS,
    DerivedPeriod,
    derive_periods,
)


@dataclass(frozen=True)
class RowComparison:
    """One payday's stored columns beside the values derived for it.

    Attributes:
        period_id: The ``budget.pay_periods.id`` the stored values came from,
            or ``None`` for an unsaved row.  Carried so a disagreement on a
            real database names the row an operator can go and look at.
        start_date: The payday.  The join key, and the only value the two
            sides share by definition rather than by agreement.
        stored_index: ``period_index`` as the table holds it.
        derived_index: The payday's position in the owner's payday order.
        stored_end: ``end_date`` as the table holds it.
        derived_end: The day before the next payday, or -- for the last
            payday -- ``start_date + cadence_days - 1``.
        end_is_projected: Whether :attr:`derived_end` came from the cadence
            rather than from a following payday.  True for the last row of a
            non-empty calendar and no other.
    """

    period_id: "int | None"
    start_date: date
    stored_index: int
    derived_index: int
    stored_end: date
    derived_end: date
    end_is_projected: bool

    @property
    def index_agrees(self) -> bool:
        """Whether the stored ordinal equals the derived one.

        Returns:
            ``True`` when the two indices match.
        """
        return self.stored_index == self.derived_index

    @property
    def end_agrees(self) -> bool:
        """Whether the stored last-covered-day equals the derived one.

        Returns:
            ``True`` when the two ends match.
        """
        return self.stored_end == self.derived_end

    @property
    def agrees(self) -> bool:
        """Whether both derived values reproduce the stored ones.

        Returns:
            ``True`` when the row is byte-identical on both columns.
        """
        return self.index_agrees and self.end_agrees

    def as_blob(self) -> dict:
        """Return a JSON-stable mapping of this row.

        Dates are rendered ISO so a blob written before a change and one
        written after diff on VALUES rather than on repr formatting.

        Returns:
            The row as plain JSON-serialisable types.
        """
        return {
            "period_id": self.period_id,
            "start_date": self.start_date.isoformat(),
            "stored_index": self.stored_index,
            "derived_index": self.derived_index,
            "stored_end": self.stored_end.isoformat(),
            "derived_end": self.derived_end.isoformat(),
            "end_is_projected": self.end_is_projected,
            "agrees": self.agrees,
        }


@dataclass(frozen=True)
class CalendarComparison:
    """One owner's whole calendar, stored beside derived.

    Attributes:
        user_id: The owner these periods belong to.
        cadence_days: The cadence the derivation was driven with.
        cadence_is_stored: Whether *cadence_days* came from the owner's
            ``budget.pay_schedule`` row.  ``False`` means it was INFERRED from
            the last period's length, and then that row's end comparison is
            CIRCULAR -- the derivation is reading back the value it produced
            (plan finding **P8**).
            **No live producer supplies ``False`` any longer** (plan step
            ``pay_calendar:C4-b-2``): ``fk_pay_periods_schedule`` makes an
            owner with paydays and no cadence row unstorable and
            ``pay_schedule_service.resolve_schedule``'s inferring arm is
            deleted, so ``tests/manual/verify_pay_calendar_derivation.py``
            now reads the stored row or nothing.  This oracle keeps the
            parameter and stays TOTAL over both values, because it is a pure
            function graded on its own terms -- a harness driving a database
            below that revision may still hand it ``False``.
        rows: One :class:`RowComparison` per payday, ``start_date`` ascending.
    """

    user_id: int
    cadence_days: int
    cadence_is_stored: bool
    rows: "tuple[RowComparison, ...]"

    @property
    def disagreements(self) -> "tuple[RowComparison, ...]":
        """Return every row whose derived values differ from its stored ones.

        Returns:
            The disagreeing rows, in ``start_date`` order.  Empty when the
            derivation reproduces the table exactly.
        """
        return tuple(row for row in self.rows if not row.agrees)

    @property
    def provable_disagreements(self) -> "tuple[RowComparison, ...]":
        """Return the disagreements that actually prove something.

        The same set as :attr:`disagreements`, minus the one comparison a
        circular cadence makes meaningless: when the cadence was INFERRED from
        the last period's own length, that row's END reproduces itself by
        arithmetic and its agreement -- or disagreement -- says nothing.  Its
        INDEX is unaffected and still counts, and so does every other row,
        because rows 0..n-2 derive from ``lead(start) - 1`` and never touch the
        cadence at all.

        Disqualifying the whole USER on an inferred cadence was this oracle's
        first cut and it was wrong: a schedule-less owner with a genuine hole
        disagreed on real rows and the harness exited 0 (found by adversarial
        review, 2026-08-08).

        Returns:
            The disagreements a verdict may be based on.
        """
        if self.cadence_is_stored or not self.rows:
            return self.disagreements
        last = self.rows[-1]
        return tuple(
            row for row in self.disagreements
            if row is not last or not row.index_agrees
        )

    @property
    def last_end_is_circular(self) -> bool:
        """Whether the final row's end comparison proves nothing.

        Returns:
            ``True`` when the cadence was inferred from the last period's own
            length, so ``start_date + cadence_days - 1`` reproduces
            ``end_date`` by arithmetic identity rather than by agreement.
        """
        return bool(self.rows) and not self.cadence_is_stored

    def as_blob(self) -> dict:
        """Return a JSON-stable mapping of the whole comparison.

        Returns:
            The comparison as plain JSON-serialisable types, including the
            summary counts an operator reads first.
        """
        return {
            "user_id": self.user_id,
            "cadence_days": self.cadence_days,
            "cadence_is_stored": self.cadence_is_stored,
            "last_end_is_circular": self.last_end_is_circular,
            "row_count": len(self.rows),
            "disagreement_count": len(self.disagreements),
            "provable_disagreement_count": len(self.provable_disagreements),
            "rows": [row.as_blob() for row in self.rows],
        }


def compare(
    user_id: int,
    periods: "Sequence[PayPeriod]",
    cadence_days: int,
    cadence_is_stored: bool,
) -> CalendarComparison:
    """Drive the derivation over *periods*' paydays and diff it leaf by leaf.

    Args:
        user_id: The owner whose calendar this is.  Every row must belong to
            them or carry no owner at all.
        periods: The owner's COMPLETE pay-period set, in any order.  A partial
            list makes the last row's derived end window-dependent (plan
            finding P14), so a caller that passes a window is measuring
            something else.
        cadence_days: The cadence to project the last period's end with.
        cadence_is_stored: Whether *cadence_days* came from a
            ``budget.pay_schedule`` row rather than being inferred from the
            data under test.  See
            :attr:`CalendarComparison.cadence_is_stored`.

    Returns:
        The :class:`CalendarComparison`, one row per payday in ``start_date``
        order.

    Raises:
        ValueError: A row belongs to a different owner.  Two owners' paydays
            merged into one list derive a calendar belonging to neither, and
            production has exactly ONE owner with paydays, so the clone run
            structurally cannot surface the mistake -- which is precisely why
            the instrument refuses it rather than recording it.
        PayCalendarError: Propagated from
            :func:`~app.services.pay_calendar.derive_periods` when the payday
            set or the cadence cannot define a calendar at all.  Not caught
            here: a comparison against an underivable schedule has no meaning,
            and swallowing it would report zero disagreements.
    """
    ordered = sorted(periods, key=lambda period: period.start_date)
    foreign = [
        period for period in ordered
        if period.user_id is not None and period.user_id != user_id
    ]
    if foreign:
        raise ValueError(
            f"compare() was given {len(foreign)} pay period(s) belonging to "
            f"another owner than user {user_id}: "
            f"{sorted({period.user_id for period in foreign})}.  A calendar "
            f"is derived from ONE owner's complete payday set."
        )
    derived = derive_periods(identified_paydays(ordered), cadence_days)
    rows = []
    for stored, period in zip(ordered, derived):
        # Both sides are sorted by payday and the derivation refuses a
        # duplicate, so the walk is one-to-one -- asserted rather than assumed,
        # because a comparator that silently mispaired would report every row
        # as a disagreement and read as a catastrophic regression.
        assert stored.start_date == period.start_date, (
            f"comparator mispaired stored payday {stored.start_date} with "
            f"derived {period.start_date}"
        )
        assert stored.id == period.period_id, (
            f"comparator mispaired stored id {stored.id} with derived "
            f"{period.period_id}"
        )
        rows.append(
            RowComparison(
                period_id=stored.id,
                start_date=stored.start_date,
                stored_index=stored.period_index,
                derived_index=period.period_index,
                stored_end=stored.end_date,
                derived_end=period.end_date,
                end_is_projected=period.end_is_projected,
            )
        )
    return CalendarComparison(
        user_id=user_id,
        cadence_days=cadence_days,
        cadence_is_stored=cadence_is_stored,
        rows=tuple(rows),
    )


def identified_paydays(
    periods: "Sequence[PayPeriod]",
) -> "list[tuple[int | None, date]]":
    """Return *periods* as the ``(period_id, payday)`` pairs the producer takes.

    One place where an ORM row becomes derivation input, so a caller cannot
    quietly drop the id and get a calendar whose periods answer "which paycheck
    does this row live in" with ``None``.

    Args:
        periods: Pay-period rows, saved or not, in any order.

    Returns:
        The pairs in the order given.
    """
    return [(period.id, period.start_date) for period in periods]


@dataclass(frozen=True)
class Perturbation:
    """A schedule with one payday moved, and the record of what moved.

    Attributes:
        moved_from: The payday that was relocated.
        moved_to: Where it went.
        rows: UNSAVED copies of the whole schedule, one payday relocated and
            every stored ``period_index`` / ``end_date`` left exactly as the
            table holds it -- which is what the table would look like if
            someone had updated ``start_date`` alone.
    """

    moved_from: date
    moved_to: date
    rows: "tuple[PayPeriod, ...]"


def perturb(periods: "Sequence[PayPeriod]") -> "Perturbation | None":
    """Return *periods* with ONE payday moved, or ``None`` when it cannot be.

    The first of the two controls plan step C1 requires: byte-identity between
    the derivation and the stored columns proves nothing unless the harness can
    be shown to REPORT a difference, so it is given one to report.

    The payday moved is the one at position ``len // 2`` -- the middle of an
    odd-length schedule and the first of the upper half otherwise, which is the
    exact middle of production's 61 rows.  It is relocated to one day before
    the earliest payday.  Two properties make that the right move rather than a
    nudge: it is collision-free by construction -- strictly earlier than every
    other payday, so it can never duplicate one whatever the cadence, where a
    one-day nudge collides on a one-day cadence -- and it moves BOTH derived
    columns at once.  Every payday it jumped over shifts one ordinal later; the
    payday it used to follow runs on to whatever now succeeds it; and its own
    span collapses to a single day.  A perturbation that moved only an end
    would leave the index half of the harness untested.

    What it does NOT perturb is the cadence, so the LAST row's projected end is
    identical before and after -- that blindness is :func:`cadence_control`'s.

    The rows are UNSAVED copies and nothing here touches the originals, which
    is what lets the manual harness run this against a real database without
    writing to it.

    Args:
        periods: The owner's complete pay-period set, in any order.

    Returns:
        The :class:`Perturbation`, or ``None`` when the schedule holds fewer
        than two paydays -- there is then no order to disturb, and a harness
        must report that the control was INAPPLICABLE rather than that it
        failed.  Every fresh signup is a one-payday user
        (``auth_service.register_user`` writes one bootstrap payday), so this
        is the common case on a real database, not a corner.
    """
    ordered = sorted(periods, key=lambda period: period.start_date)
    if len(ordered) < 2:
        return None
    moved_position = len(ordered) // 2
    moved_from = ordered[moved_position].start_date
    moved_to = ordered[0].start_date - timedelta(days=1)
    return Perturbation(
        moved_from=moved_from,
        moved_to=moved_to,
        rows=tuple(
            PayPeriod(
                id=period.id,
                user_id=period.user_id,
                period_index=period.period_index,
                start_date=moved_to if position == moved_position
                else period.start_date,
                end_date=period.end_date,
            )
            for position, period in enumerate(ordered)
        ),
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
            pre-normalization defect, which reproduces production's regular
            schedule byte-identically and is invisible to every other check
            here.
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

    The second control, and the one that measures the branch the live data
    cannot.  ``end_date`` has two sources -- the next payday for every period
    but the last, the cadence for the last -- and on production's perfectly
    regular schedule the two AGREE on all 61 rows.  So a derivation that took
    the cadence branch everywhere reproduces the clone exactly.  Measured
    during this step's review: that mutant fails 16 suite tests and the clone
    harness still exits 0.

    Changing the cadence by one day separates them: with the branch correct
    exactly one end moves, by exactly one day.  With the branch wrong every end
    moves.  With the branch stubbed to reuse the stored value nothing moves.

    Args:
        paydays: The owner's complete payday set as ``(period_id, payday)``
            pairs, in any order (:func:`identified_paydays` builds them from
            ORM rows).
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


def verdict(
    comparison: CalendarComparison,
    payday: "Perturbation | None",
    cadence: CadenceControl,
) -> "tuple[bool, tuple[str, ...]]":
    """Decide whether one owner's run passed, and say why when it did not.

    The pass/fail rule, in one tested place.  It lived in the manual script and
    carried two defects there, both found by adversarial review 2026-08-08: it
    disqualified a whole USER on an inferred cadence when only one row's end is
    circular, and it scored an INAPPLICABLE control as a failed one -- which,
    since every fresh signup is a one-payday user, meant any database holding a
    new account could never go green.

    An inapplicable payday control is not a pass either; it is simply not a
    failure of THIS owner.  The caller checks that at least one owner's control
    was applicable, so a database of nothing but one-payday users cannot be
    reported as proven.

    Args:
        comparison: The owner's stored-versus-derived diff.
        payday: The payday control's perturbed schedule, or ``None`` when the
            schedule was too short to disturb.
        cadence: The cadence control's outcome.

    Returns:
        ``(passed, reasons)`` -- ``reasons`` is empty when *passed* is ``True``
        and otherwise names every rule that failed.
    """
    reasons: "list[str]" = []
    if comparison.provable_disagreements:
        reasons.append(
            f"{len(comparison.provable_disagreements)} row(s) disagree with "
            f"the stored columns"
        )
    if payday is not None:
        moved = compare(
            comparison.user_id,
            payday.rows,
            comparison.cadence_days,
            comparison.cadence_is_stored,
        )
        if not moved.disagreements:
            reasons.append(
                "the payday control reported nothing: one payday was moved "
                f"from {payday.moved_from} to {payday.moved_to} and the "
                "comparator still found the calendar unchanged"
            )
    if cadence.applicable and not cadence.fired:
        reasons.append(
            f"the cadence control reported {len(cadence.moved)} moved end(s) "
            f"at probe cadence {cadence.probe_cadence}; exactly one, shifted "
            f"by {cadence.expected_shift_days} day(s), is the only correct "
            "outcome"
        )
    return not reasons, tuple(reasons)


@dataclass(frozen=True)
class IrregularShape:
    """One schedule the live data cannot supply, with its hand-computed answer.

    The generated sweep plan step C1 asks for.  Production is contiguous, every
    period is exactly 14 days long and there is exactly one cadence, so a run
    over the clone exercises ONE shape however many rows it has.  These are the
    others -- each with the stored columns as a table would hold them and the
    derived values worked out by hand, so the sweep asserts VALUES rather than
    agreeing with whatever the code produced.

    Attributes:
        label: Short identifier, used as the test id.
        why: What this shape exists to pin, in one sentence.
        cadence_days: The cadence the derivation is driven with.
        stored: ``(period_id, period_index, start_date, end_date)`` per row,
            exactly as ``budget.pay_periods`` would hold them.  The ids are
            written out rather than generated so ``index_out_of_date_order``
            can pin that a row's IDENTITY follows its payday through the sort
            while its stored ordinal does not.
        expected: The hand-computed derivation, in ``start_date`` order.
        disagreeing_starts: The paydays whose stored columns the derivation
            contradicts.  Empty means the shape must reproduce byte-identically.
    """

    label: str
    why: str
    cadence_days: int
    stored: "tuple[tuple[int, int, date, date], ...]"
    expected: "tuple[DerivedPeriod, ...]"
    disagreeing_starts: "tuple[date, ...]"

    @property
    def paydays(self) -> "list[tuple[int | None, date]]":
        """Return the shape's ``(period_id, payday)`` pairs, as listed.

        Returns:
            One pair per stored row, in the order the shape declares them --
            deliberately not sorted, so every use of this also exercises the
            derivation's own ordering.
        """
        return [
            (period_id, start_date)
            for period_id, _index, start_date, _end in self.stored
        ]


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


def build_stored_rows(
    shape_to_build: IrregularShape, user_id: int,
) -> "list[PayPeriod]":
    """Return *shape_to_build*'s stored rows as UNSAVED ``PayPeriod`` instances.

    Real model instances rather than a hand-rolled double, for the reason
    ``tests/oracles/recurrence_baseline.py`` records at length: a double that
    mirrors the model drifts from it silently, while a column the model does
    not have raises ``TypeError`` here instead of passing.  Unsaved costs no
    session and no flush.

    Unsaved is also the only way to build the ``one_day_periods`` shape at all
    -- ``ck_pay_periods_date_order CHECK (start_date < end_date)`` rejects a
    period whose end equals its start (plan finding P9).  The other nine shapes
    would all satisfy every live constraint and could be INSERTed; what refuses
    them is the service write door, not the schema.  (An earlier draft of this
    docstring said "several of these shapes", which the review of this step
    measured as one.)

    Args:
        shape_to_build: The schedule shape to instantiate.
        user_id: The owner to stamp on the rows.

    Returns:
        The rows in the order the shape lists them, unsaved and unattached.
    """
    return [
        PayPeriod(
            id=period_id,
            user_id=user_id,
            period_index=period_index,
            start_date=start_date,
            end_date=end_date,
        )
        for period_id, period_index, start_date, end_date
        in shape_to_build.stored
    ]


#: The generated sweep.  Coverage is STATED rather than implied (the plan's
#: "no silent caps" rule): every branch the derivation has -- the ``lead`` end,
#: the projected end, the empty schedule -- against every schedule irregularity
#: the arc has named.  The four shapes that DISAGREE are four defects made
#: visible: a hole (plan finding P2), a shared boundary day (P4's blind spot),
#: a stored cadence the schedule has outgrown (P12), and an index out of date
#: order, which has no ledger row of its own because it is the state section
#: 1's fence table exists for.
IRREGULAR_SHAPES: "tuple[IrregularShape, ...]" = (
    IrregularShape(
        label="empty",
        why=(
            "A user who has never generated a schedule, and the companion "
            "role, which by design holds no paydays of its own -- measured on "
            "production 2026-08-08, user 2 has zero."
        ),
        cadence_days=14,
        stored=(),
        expected=(),
        disagreeing_starts=(),
    ),
    IrregularShape(
        label="single_payday",
        why=(
            "The one schedule where EVERY end is projected, and the state "
            "registration leaves a new owner in (auth_service writes one "
            "bootstrap payday).  It is also the only shape for which the P8 "
            "cadence inference is genuinely unresolvable."
        ),
        cadence_days=14,
        # 2026-03-26 + 13 days = 2026-04-08.  Production's own first payday.
        stored=((1, 0, date(2026, 3, 26), date(2026, 4, 8)),),
        expected=(
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 3, 26),
                end_date=date(2026, 4, 8),
                end_is_projected=True,
            ),
        ),
        disagreeing_starts=(),
    ),
    IrregularShape(
        label="biweekly_five",
        why=(
            "The ordinary schedule, and the baseline every control is "
            "measured against: five contiguous fortnights, which is what the "
            "writer produces and what production is 61 of."
        ),
        cadence_days=14,
        stored=(
            (1, 0, date(2026, 1, 2), date(2026, 1, 15)),
            (2, 1, date(2026, 1, 16), date(2026, 1, 29)),
            (3, 2, date(2026, 1, 30), date(2026, 2, 12)),
            (4, 3, date(2026, 2, 13), date(2026, 2, 26)),
            (5, 4, date(2026, 2, 27), date(2026, 3, 12)),
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
        disagreeing_starts=(),
    ),
    IrregularShape(
        label="leap_day_span",
        why=(
            "February 29 inside a period's span rather than argued about -- "
            "2028 is the next leap year the live schedule reaches (its last "
            "payday is 2028-07-13)."
        ),
        cadence_days=14,
        # 2028-02-18 + 13 days crosses Feb 29 -> 2028-03-02.
        # 2028-03-03 + 13 days = 2028-03-16.
        stored=(
            (1, 0, date(2028, 2, 18), date(2028, 3, 2)),
            (2, 1, date(2028, 3, 3), date(2028, 3, 16)),
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
        disagreeing_starts=(),
    ),
    IrregularShape(
        label="one_day_periods",
        why=(
            "Two paydays a day apart are two facts and the period between "
            "them is one day long -- which ck_pay_periods_date_order "
            "(start_date < end_date) makes illegal, plan finding P9.  The "
            "derivation produces it without complaint; the constraint is the "
            "artifact."
        ),
        cadence_days=1,
        # At cadence 1 the writer's own arithmetic is end = start + 0.
        stored=(
            (1, 0, date(2026, 1, 1), date(2026, 1, 1)),
            (2, 1, date(2026, 1, 2), date(2026, 1, 2)),
            (3, 2, date(2026, 1, 3), date(2026, 1, 3)),
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
        disagreeing_starts=(),
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
        stored=(
            (1, 0, date(2026, 1, 1), date(2026, 3, 31)),
            (2, 1, date(2026, 4, 1), date(2026, 6, 29)),
            (3, 2, date(2026, 6, 30), date(2026, 9, 27)),
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
        disagreeing_starts=(),
    ),
    IrregularShape(
        label="hole",
        why=(
            "Plan finding P2: the writer accepts a batch that leaves a day "
            "funded by no paycheck -- here 2026-01-30 through 2026-02-12.  "
            "The derivation CLOSES it, because there is no second column to "
            "disagree with, which is the whole normalization argument in one "
            "row."
        ),
        cadence_days=14,
        stored=(
            (1, 0, date(2026, 1, 2), date(2026, 1, 15)),
            (2, 1, date(2026, 1, 16), date(2026, 1, 29)),
            (3, 2, date(2026, 2, 13), date(2026, 2, 26)),
        ),
        expected=(
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 15),
                end_is_projected=False,
            ),
            # lead - 1 = 2026-02-13 - 1 = 2026-02-12, NOT the stored
            # 2026-01-29: the fourteen unfunded days join the period before
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
        disagreeing_starts=(date(2026, 1, 16),),
    ),
    IrregularShape(
        label="shared_boundary_day",
        why=(
            "Plan finding P4: integrity_check BA-04's predicate is "
            "p2.start_date < p1.end_date, so two periods sharing EXACTLY one "
            "boundary day pass the weekly check (docs/runbook.md: 3:30 AM "
            "Sunday).  2026-01-15 is covered twice here and the derivation "
            "cannot express it."
        ),
        cadence_days=14,
        stored=(
            (1, 0, date(2026, 1, 2), date(2026, 1, 15)),
            (2, 1, date(2026, 1, 15), date(2026, 1, 28)),
        ),
        expected=(
            # lead - 1 = 2026-01-15 - 1 = 2026-01-14: the doubled day is gone.
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
        disagreeing_starts=(date(2026, 1, 2),),
    ),
    IrregularShape(
        label="index_out_of_date_order",
        why=(
            "A stored ordinal that disagrees with payday order.  NEITHER "
            "table constraint keeps it out -- uq_pay_periods_user_index "
            "refuses a DUPLICATE ordinal and ck_pay_periods_positive_index a "
            "NEGATIVE one, and 0 and 1 here are neither.  What refuses it is "
            "_pp_assert_structure in the suite, which C4 deletes -- the "
            "recurrence arc's own value-boundary refusal went at C2-b2 with "
            "the class that held it.  Derived, the ordinal IS the payday "
            "order -- and the row "
            "IDs prove it, running 2 then 1 down the derived calendar."
        ),
        cadence_days=14,
        stored=(
            (1, 0, date(2026, 1, 30), date(2026, 2, 12)),
            (2, 1, date(2026, 1, 2), date(2026, 1, 15)),
        ),
        expected=(
            # 2026-01-02 sorts first, so it is index 0 -- carrying id 2, the
            # id of the row that stored ordinal 1.  Its end runs to the day
            # before the next payday: 2026-01-30 - 1 = 2026-01-29.
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
        disagreeing_starts=(date(2026, 1, 2), date(2026, 1, 30)),
    ),
    IrregularShape(
        label="cadence_changed_mid_schedule",
        why=(
            "regenerate_pay_periods keeps a head at the old cadence and "
            "rebuilds the tail at a new one, so a live schedule can hold two.  "
            "Only the LAST end reads cadence_days, so every other row "
            "reproduces exactly -- which is why a cadence change is not a "
            "hazard to the derivation and P12 is."
        ),
        cadence_days=7,
        stored=(
            (1, 0, date(2026, 1, 2), date(2026, 1, 15)),
            (2, 1, date(2026, 1, 16), date(2026, 1, 29)),
            (3, 2, date(2026, 1, 30), date(2026, 2, 5)),
            (4, 3, date(2026, 2, 6), date(2026, 2, 12)),
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
        disagreeing_starts=(),
    ),
    IrregularShape(
        label="stored_cadence_no_longer_matches",
        why=(
            "Plan finding P12 made visible: a /pay-periods/generate post "
            "naming an already-existing payday with num_periods=1 creates "
            "ZERO rows and still reaches upsert_schedule, so cadence_days is "
            "rewritten by a batch that wrote nothing.  The periods were "
            "written at 14 and the schedule now says 7, which moves the "
            "horizon seven days with nothing written -- and moves EXACTLY the "
            "one end DerivedPeriod.end_is_projected marks."
        ),
        cadence_days=7,
        stored=(
            (1, 0, date(2026, 1, 2), date(2026, 1, 15)),
            (2, 1, date(2026, 1, 16), date(2026, 1, 29)),
        ),
        expected=(
            DerivedPeriod(
                period_id=1,
                period_index=0,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 15),
                end_is_projected=False,
            ),
            # projected at the REWRITTEN cadence: 2026-01-16 + 6 =
            # 2026-01-22, seven days short of the stored 2026-01-29.
            DerivedPeriod(
                period_id=2,
                period_index=1,
                start_date=date(2026, 1, 16),
                end_date=date(2026, 1, 22),
                end_is_projected=True,
            ),
        ),
        disagreeing_starts=(date(2026, 1, 16),),
    ),
)
