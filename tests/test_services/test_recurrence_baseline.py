"""The recurrence baseline gate and its firing controls (plan step R1).

``tests/oracles/recurrence_baseline.py`` captures what the CURRENT recurrence
engine answers for every rule shape; this module freezes that capture against a
committed snapshot and proves the freeze can actually fail.

The plan (``docs/plans/implementation_plan_recurrence_redesign.md``) makes this
the gate for R3 (the new occurrence engine, built parallel and unread) and R4
(the cutover).  A snapshot test that cannot fail would hand both steps a free
pass that reads as proof, so the two control tests below are not decoration:
they patch the engine and assert the blob moves.

**Regenerating.**  When a step's design says the baseline moves, re-run with
``SHEKEL_UPDATE_RECURRENCE_BASELINE=1`` and commit the diff WITH the change, so
every moved line is reviewable beside the code that moved it:

    SHEKEL_UPDATE_RECURRENCE_BASELINE=1 ./scripts/test.sh \\
        tests/test_services/test_recurrence_baseline.py

Regenerating to make a red test green is the thing this gate exists to prevent
(CLAUDE.md rule 5).  A moved line is a behaviour change until its step's design
says otherwise.

**A regeneration run is never GREEN, since plan step R-F13** (ledger row F-13).
The gate SKIPS while the variable is set, and a skip reads as a pass in every
summary anyone looks at -- so an exported variable used to turn the 430-shape
gate off and rewrite the snapshot it defends, with nothing saying so.
:meth:`TestRecurrenceBaseline.test_the_regeneration_switch_is_off` fails for
exactly as long as the switch is on, so the rewritten blob has to be compared
by a second run before any suite can report success.
"""

import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services import recurrence_engine
from app.services.recurrence import _reading
from tests.oracles import recurrence_baseline

#: The committed snapshot.  Lives beside the harness that writes it.
BASELINE_PATH = Path(recurrence_baseline.__file__).with_suffix(".txt")

#: Set to regenerate rather than compare.  Named in full so it cannot be
#: exported by accident from a shell that also runs the rest of the suite.
_UPDATE_ENV = "SHEKEL_UPDATE_RECURRENCE_BASELINE"


def _regeneration_requested() -> str | None:
    """Return the regeneration switch's value, or ``None`` when it is off.

    One reader for the switch, so the gate that OBEYS it and the control that
    refuses to let a run be green while it is on cannot disagree about what
    "set" means.  An empty string is off: an exported-but-blank variable is
    how a shell says nothing.

    Returns:
        The switch's value, or ``None``.
    """
    return os.environ.get(_UPDATE_ENV) or None


class TestRecurrenceBaseline:
    """The frozen baseline itself."""

    def test_the_baseline_has_not_moved(self):
        """Every shape answers exactly what the committed snapshot records.

        The gate.  A failure means the recurrence engine's behaviour changed;
        the diff names which shapes and which occurrences moved.
        """
        captured = recurrence_baseline.capture_baseline()

        if _regeneration_requested():
            BASELINE_PATH.write_text(captured, encoding="utf-8")
            pytest.skip(
                f"{_UPDATE_ENV} set -- rewrote {BASELINE_PATH.name}. "
                "Review the diff and commit it with the change that moved it."
            )

        assert BASELINE_PATH.exists(), (
            f"{BASELINE_PATH} is missing. Regenerate with "
            f"{_UPDATE_ENV}=1 and commit it."
        )
        expected = BASELINE_PATH.read_text(encoding="utf-8")

        # Compare line by line: the blob is thousands of lines, and pytest's
        # whole-string diff on a mismatch is unreadable at that size.  The
        # first differing line is what a reader needs.
        captured_lines = captured.splitlines()
        expected_lines = expected.splitlines()
        for line_no, (got, want) in enumerate(
            zip(captured_lines, expected_lines), start=1,
        ):
            assert got == want, (
                f"{BASELINE_PATH.name} line {line_no} moved:\n"
                f"  committed: {want}\n"
                f"  captured:  {got}"
            )
        assert len(captured_lines) == len(expected_lines), (
            f"{BASELINE_PATH.name} changed length: committed "
            f"{len(expected_lines)} lines, captured {len(captured_lines)}. "
            "A shape was added or removed."
        )

    def test_the_regeneration_switch_is_off(self):
        """A run with the switch ON is never GREEN (ledger row F-13).

        The gate above SKIPS when :data:`_UPDATE_ENV` is set, and a skip reads
        as a pass in every summary the developer and CI look at -- so an
        exported variable turned the 430-shape gate off and rewrote the
        snapshot it was supposed to defend, silently.  That is the failure
        CLAUDE.md rule 5 names, automated.

        **This test is EXPECTED to fail during a deliberate regeneration**, and
        that is the design rather than a wart: regenerating is not validating,
        so the run that rewrites the baseline must not be able to report
        success.  Unset the variable and re-run to get a green suite, which is
        also the moment the rewritten snapshot is actually compared.
        """
        assert _regeneration_requested() is None, (
            f"{_UPDATE_ENV} is set, so the baseline gate SKIPPED and the "
            f"snapshot was rewritten instead of compared.  A skip reads as a "
            f"pass: this run proves nothing about the recurrence engine.  "
            f"Review the rewritten tests/oracles/recurrence_baseline.txt, "
            f"unset {_UPDATE_ENV}, and run again."
        )

    def test_the_baseline_is_not_trivially_small(self):
        """The snapshot covers the shape space it claims to.

        Guards the failure mode where a builder silently stops contributing and
        the gate keeps passing over a shrunken shape set -- the "0 bugs found"
        shape of a green run.  The floor is well below the real count so it
        does not need editing whenever a shape is added.
        """
        shapes = recurrence_baseline.build_shapes()
        labels = [shape.label for shape in shapes]

        assert len(shapes) > 400, f"only {len(shapes)} shapes"
        assert len(set(labels)) == len(labels), "duplicate shape labels"
        # Every builder's prefix is represented, so a builder that stopped
        # appending is caught by name rather than by a count nobody reads.
        for prefix in (
            "annual.", "bounds.", "due_sweep.", "every_n_periods.",
            "every_period", "long_cadence.", "monthly.", "monthly_first",
            "quarterly.", "semi_annual.",
        ):
            assert any(label.startswith(prefix) for label in labels), (
                f"no shape with prefix {prefix!r} -- a builder stopped "
                "contributing and the gate would still pass"
            )

    def test_the_schedule_is_contiguous(self):
        """The baseline schedule tiles the calendar with no gap or overlap.

        The redesign's forward placement (R3) depends on every date falling in
        exactly one period.  If the harness built a schedule that did not tile,
        the baseline would freeze answers against a schedule the app cannot
        produce, and R4's diff would be measured against fiction.
        """
        periods = recurrence_baseline.build_schedule(
            recurrence_baseline.SCHEDULE_START,
            recurrence_baseline.SCHEDULE_CADENCE_DAYS,
            recurrence_baseline.SCHEDULE_PERIOD_COUNT,
        )

        assert len(periods) == recurrence_baseline.SCHEDULE_PERIOD_COUNT
        for earlier, later in zip(periods, periods[1:]):
            assert later.start_date == earlier.end_date + timedelta(days=1), (
                f"gap or overlap between period {earlier.period_index} "
                f"(ends {earlier.end_date}) and {later.period_index} "
                f"(starts {later.start_date})"
            )
            assert later.period_index == earlier.period_index + 1

    def test_the_schedule_covers_a_leap_day(self):
        """February 29 is inside the captured span.

        Month-end clamping is the engine's subtlest behaviour and the one the
        redesign must preserve exactly (the live ``Walmart+`` rule is day 31).
        A baseline that never crossed a leap day would freeze the easy half.
        """
        periods = recurrence_baseline.build_schedule(
            recurrence_baseline.SCHEDULE_START,
            recurrence_baseline.SCHEDULE_CADENCE_DAYS,
            recurrence_baseline.SCHEDULE_PERIOD_COUNT,
        )
        leap_day = date(2024, 2, 29)

        assert any(
            period.start_date <= leap_day <= period.end_date
            for period in periods
        ), "no period contains 2024-02-29"


class TestBaselineFiringControls:
    """The gate must FAIL when the engine changes -- shown, not asserted.

    Verification standard: "every guard gets a negative control that is shown
    to fire" and "ask of every harness: can it SEE the code under test?"
    (``docs/audits/balance_architecture/README.md`` Section 7.2).  Both
    controls patch the SOURCE module, which is the only patch target that
    proves the harness resolves the engine at call time rather than having
    bound it at import.
    """

    def test_a_changed_due_date_moves_the_blob(self, monkeypatch):
        """Shifting every due date by a day changes the capture."""
        before = recurrence_baseline.capture_baseline()
        real_compute = recurrence_engine.compute_due_date

        def shifted(rule, period):
            return real_compute(rule, period) + timedelta(days=1)

        monkeypatch.setattr(recurrence_engine, "compute_due_date", shifted)
        after = recurrence_baseline.capture_baseline()

        assert after != before, (
            "the harness did not see a changed compute_due_date -- it is "
            "blind to the code it exists to freeze"
        )

    def test_a_changed_matcher_moves_the_blob(self, monkeypatch):
        """Dropping one occurrence from every rule changes the capture.

        Patches the DEFINITION site
        (``app.services.recurrence._reading.rule_occurrences``), not the
        package's re-export of it.  A neutral review of plan step R4b-2 caught
        the difference: the oracle reaches the producer through the same alias,
        so patching the alias would have proved only that the harness reads the
        attribute it reads.  Patching where the function is DEFINED proves the
        harness resolves the real implementation at call time.  Plan step R4b-2
        moved the target: ``recurrence_engine.match_periods`` is deleted.
        """
        before = recurrence_baseline.capture_baseline()
        real_occurrences = _reading.rule_occurrences

        def one_fewer(rule, calendar):
            return real_occurrences(rule, calendar)[1:]

        monkeypatch.setattr(_reading, "rule_occurrences", one_fewer)
        after = recurrence_baseline.capture_baseline()

        assert after != before, (
            "the harness did not see a changed rule_occurrences -- it is "
            "blind to the code it exists to freeze"
        )

    def test_the_switch_check_sees_the_variable(self, monkeypatch):
        """The switch check reads the environment at CALL time.

        The control for :meth:`TestRecurrenceBaseline
        .test_the_regeneration_switch_is_off`, and it is the same question the
        two controls above ask of the harness: a check bound at import would
        pass whatever the shell did.  Both directions, because an
        exported-but-blank variable means the switch is off and reading it as
        on would fail every run in a shell that had merely mentioned it.
        """
        monkeypatch.setenv(_UPDATE_ENV, "1")
        assert _regeneration_requested() == "1", (
            "the switch check does not see a set variable, so the assertion "
            "that keeps a regeneration run from reading as green is inert"
        )

        monkeypatch.setenv(_UPDATE_ENV, "")
        assert _regeneration_requested() is None

    def test_the_capture_is_stable_across_runs(self):
        """Two captures with nothing changed are byte-identical.

        A baseline that varies run to run would fail the gate at random and be
        regenerated until it passed, which is how a gate becomes a ritual.
        Reads no clock, so this holds under both CI clock zones.
        """
        assert (
            recurrence_baseline.capture_baseline()
            == recurrence_baseline.capture_baseline()
        )
