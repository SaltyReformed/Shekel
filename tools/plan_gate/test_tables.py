"""Controls for the table GRAMMAR: which rows a registry reader admits.

The sibling of :mod:`_tables`, split from ``test_registry_integrity.py`` on
2026-08-14 for the reason its own docstring gives about that module: it stands
at pylint's 1,000-line ceiling, and this project's ruling on an over-ceiling
module is that it splits rather than being shaved (findings **N-152** /
**N-156** / **N-201**).

What belongs here is the reader's own contract -- what IS a row of this
registry -- as against ``test_registry_integrity``'s subject, which is what a
registry's rows must SATISFY once read.  Finding **N-234** is exactly why the
distinction earns a file: the arms were sound and the reader was handing them
rows from a table that was not theirs.
"""
from __future__ import annotations

import pytest

import _registry as registry
from _staging import stage_a_fork


class TestATableIsFoundByItsHeader:
    """Finding **N-234**: a table's identity is its header, never its width."""

    def test_a_new_three_column_table_is_not_read_as_a_fork(self, stage):
        """A table added for another purpose does not join the fork registry.

        ``forks()`` took every three-column row in ``steps.md`` and
        ``step_rows()`` every seven-column one, so a table added to that file
        for any other purpose joined whichever registry it happened to match --
        silently, and with no error to read.  It fired on the first edit that
        tried: a ``| arc | document | section |`` reference table read as four
        unruled forks and turned four fork controls red.

        The table planted here is the shape a reader would actually add, and it
        is planted at the FOOT of the file so it lands after the real forks
        table -- which is where the width-keyed reader would have picked it up.
        """
        stage_a_fork(stage)
        before = len(registry.forks())
        assert before, "the premise: the corpus states forks to be diluted"
        anchor = "## Cross-arc forks"
        stage(
            "steps", anchor,
            "## A reference table someone added\n\n"
            "| arc | document | section |\n"
            "|---|---|---|\n"
            "| balance | README.md | 5 |\n"
            "| recurrence | implementation_plan_recurrence_redesign.md | 4 |\n"
            "\n" + anchor,
        )

        assert len(registry.forks()) == before, (
            f"a three-column reference table joined the fork registry: "
            f"{[fork.defect for fork in registry.forks()]}"
        )

    def test_the_three_step_sections_are_read_as_one_registry(self):
        """Order, containers and shipped share a header, so they share a reader.

        The other half of the rule above, and the reason the fix is a header
        rather than a heading: three SECTIONS of ``steps.md`` hold the same kind
        of row, and a heading-anchored reader would have needed all three named.
        """
        states = {row.state for row in registry.step_rows()}

        assert "SHIPPED" in states, "the shipped section is not being read"
        assert "container" in states, "the containers section is not being read"
        assert any(row.rank is not None for row in registry.step_rows()), (
            "the order section is not being read"
        )

    def test_a_renamed_header_is_an_error_rather_than_an_empty_registry(
        self, stage,
    ):
        """A table the parser cannot find must not read as a table with no rows.

        The failure mode a header-anchored reader introduces, and the reason
        :func:`_tables.rows_under` asserts: every predicate over a registry
        passes vacuously when it holds nothing, so a restructured document would
        turn the whole gate green.  Rule 3's count arm would catch it for the
        two registries that state their own size, and forks state none.
        """
        stage("steps", "| defect | competing remedies | ruled |",
              "| defect | competing options | ruled |")

        with pytest.raises(AssertionError, match="no table with the header"):
            registry.forks()
