"""
Shekel Budget App -- Tests for ``scripts/check_tax_law.py`` (plan step salary:X-at-4).

The command every GitHub alarm runs (rulings salary:R-SAL74, R-SAL88): the
weekly watch at the NOTICE stage (fails from November 1), and at the REFUSE stage
(fails from December 1) CI's ``tax-law`` job and the check before the release
image is built.  Its exit status IS the alarm, so every case here asserts the
status beside the words.

No test reads the clock: ``report`` takes the day, and ``main`` reads
:func:`app.utils.dates.display_today`, which the tests replace.  Every law is
made up, so nothing moves when a real year is published.
"""

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from app.services.tax_law_alarm import Stage
from app.utils import dates
from scripts.check_tax_law import main, report
from tests._test_helpers import EMPTY_TAX_LAW, made_up_law

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_tax_law.py"

_THROUGH_2026 = made_up_law((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"}))

_HOW_TO_ADD = (
    "Add it: a year is a new app/tax_law/_year_<YYYY>.py listed in LAW "
    "(app/tax_law/__init__.py); a state goes in its year's module.  "
    "Transcribe from the published sources each year module cites."
)


class TestTheReport:
    """``report`` judges a law on a day for a stage: the lines and the exit status."""

    def test_a_complete_law_passes_and_says_so(self):
        """Before November 1 the notice asks only for this year, which is in."""
        lines, status = report(_THROUGH_2026, date(2026, 10, 31), Stage.NOTICE)

        assert status == 0
        assert lines == [
            "The tax law carries: 2025, 2026.",
            "Today (America/New_York) is 2026-10-31; the notice alarm asks for the law "
            "through 2026.",
            "OK: the tax law is complete through 2026.",
        ]

    def test_a_missing_year_fails_and_names_it(self):
        """From November 1 the weekly watch fails until next year is in."""
        lines, status = report(_THROUGH_2026, date(2026, 11, 1), Stage.NOTICE)

        assert status == 1
        assert lines == [
            "The tax law carries: 2025, 2026.",
            "Today (America/New_York) is 2026-11-01; the notice alarm asks for the law "
            "through 2027.",
            "MISSING: the whole 2027 tax law; priced on 2026's rules meanwhile.",
            _HOW_TO_ADD,
        ]

    def test_the_refusal_waits_for_december_1(self):
        """In November the notice fails and CI's refusal still passes; on December 1 both fail."""
        assert report(_THROUGH_2026, date(2026, 11, 30), Stage.NOTICE)[1] == 1
        assert report(_THROUGH_2026, date(2026, 11, 30), Stage.REFUSE)[1] == 0
        assert report(_THROUGH_2026, date(2026, 12, 1), Stage.REFUSE)[1] == 1

    def test_a_missing_state_fails_and_names_it(self):
        """A year shipped federal-first keeps the alarm red until its state lands (R-SAL86)."""
        law = made_up_law((2026, {"NC": "0.0399"}), (2027, {}))

        lines, status = report(law, date(2026, 12, 1), Stage.REFUSE)

        assert status == 1
        assert lines[2:] == [
            "MISSING: NC in the 2027 tax law; priced on 2026's rules meanwhile.",
            _HOW_TO_ADD,
        ]

    def test_a_missing_year_whose_state_prices_older_names_both(self):
        """2027 shipped without NC, then 2028 not at all: NC's 2028 line names 2026, not 2027."""
        law = made_up_law((2026, {"NC": "0.0399"}), (2027, {}))

        lines, status = report(law, date(2027, 11, 1), Stage.NOTICE)

        assert status == 1
        assert lines[2:] == [
            "MISSING: NC in the 2027 tax law; priced on 2026's rules meanwhile.",
            "MISSING: the whole 2028 tax law; priced on 2027's rules meanwhile, except NC "
            "on 2026's.",
            _HOW_TO_ADD,
        ]

    def test_a_law_with_no_year_fails_with_nothing_to_price_it(self):
        """No release ships one; the words must still be true of it."""
        lines, status = report(EMPTY_TAX_LAW, date(2026, 11, 1), Stage.NOTICE)

        assert status == 1
        assert lines[0] == "The tax law carries: no year."
        assert lines[2] == "MISSING: the whole 2027 tax law; no year prices it meanwhile."


class TestTheCommandLine:
    """``main`` reads the shipped law and the display-timezone day, prints, returns the status."""

    @pytest.mark.parametrize(("stage", "status"), [("notice", 1), ("refuse", 0)])
    def test_the_stage_named_is_the_stage_judged(
        self, monkeypatch, tax_law, capsys, stage, status,
    ):
        """November 15, no 2027: the weekly watch fails and CI's refusal does not."""
        tax_law(_THROUGH_2026)
        monkeypatch.setattr(dates, "display_today", lambda: date(2026, 11, 15))

        assert main([stage]) == status
        assert f"the {stage} alarm asks for the law through" in capsys.readouterr().out

    @pytest.mark.parametrize(("stage", "epoch"), [
        # 2026-12-01 00:00 EST and 2026-11-01 00:00 EDT, as Unix time.
        ("refuse", 1796101200), ("notice", 1793505600),
    ])
    def test_starts_epoch_is_midnight_eastern_on_the_stage_date(
        self, tax_law, capsys, stage, epoch,
    ):
        """What the release image's build compares its clock against (ruling R-SAL88)."""
        tax_law(_THROUGH_2026)

        assert main([stage, "--starts-epoch"]) == 0
        assert capsys.readouterr().out == f"{epoch}\n"

    def test_starts_epoch_is_zero_for_a_law_with_no_year(self, tax_law, capsys):
        """Its alarm speaks on every date, so the build refuses whenever it runs."""
        tax_law(EMPTY_TAX_LAW)

        assert main(["refuse", "--starts-epoch"]) == 0
        assert capsys.readouterr().out == "0\n"

    def test_an_unknown_stage_is_a_usage_error(self, capsys):
        """argparse refuses it with status 2, before any law is judged."""
        with pytest.raises(SystemExit) as exit_info:
            main(["someday"])

        assert exit_info.value.code == 2
        assert "invalid choice: 'someday'" in capsys.readouterr().err

    def test_it_runs_as_the_workflows_run_it(self):
        """``python scripts/check_tax_law.py`` imports the app from any working directory.

        ``--help`` exits before the clock is read, so this is the one run of
        the real file that no calendar can move.
        """
        completed = subprocess.run(
            [sys.executable, str(_SCRIPT), "--help"],
            capture_output=True, text=True, check=False, cwd=_SCRIPT.parents[1] / "docs",
        )

        assert completed.returncode == 0, completed.stderr
        assert "{notice,refuse}" in completed.stdout
