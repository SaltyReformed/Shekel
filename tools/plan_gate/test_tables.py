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
import _rulings as rulings
from _tables import cells
from _staging import (
    a_live_ledger_row,
    an_open_step_key,
    row_of,
    stage_a_fork,
    with_cell,
)


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


class TestAMisWidthRowIsRefused:
    """A row that does not split to its header's width is REFUSED, never dropped.

    Until 2026-09-14 :func:`_tables.rows_under` skipped such a row, and the
    control in ``test_registry_integrity`` asserted that the row VANISHES and
    rule 3's count arm reports the loss.  That defence was measured false the
    same day: an unescaped ``|`` in ``pay_calendar:PC-515`` dropped the row,
    the stated counts were then set FROM the lossy parse (``set_counts.py``
    reads these producers), and every arm was green with a finding gone.  The
    developer ruled a control per registry, named by file and row
    (2026-09-14); the reader refusing is the root of it, and these grade the
    refusal on the defect's axis -- the live files parsing clean is what every
    other arm already proves by reading them.  The rulings table's control
    lives beside its own fixture in ``test_rulings``.
    """

    def test_an_unescaped_pipe_in_a_ledger_row_is_refused_by_file_line_and_row(
        self, stage,
    ):
        """The ledger's refusal names ``ledger.md``, the line and the row.

        The expected line number is counted with ``split("\\n")`` rather than
        the producer's ``splitlines()``, so the two sides of the equality do
        not share one producer.
        """
        line = row_of("ledger", a_live_ledger_row())
        number = line_number_of(registry.LEDGER, line)
        staged = with_cell(line, 3, "an unescaped X | Y pipe")
        stage("ledger", line, staged)
        with pytest.raises(AssertionError) as refusal:
            registry.ledger_rows()
        assert_refusal_names(str(refusal.value), "ledger.md", number, line, staged)

    def test_an_unescaped_pipe_in_a_step_row_is_refused_too(self, stage):
        """The same refusal on ``steps.md``, whose rows the order arms read.

        Its own control because the ledger's refusal proves nothing about the
        steps table: ``rows_under`` is one function, but the header it is
        anchored on is what names the document.
        """
        line = row_of("steps", f"| {an_open_step_key().replace(':', ' | ')} |")
        number = line_number_of(registry.STEPS, line)
        staged = with_cell(line, 3, "a sentence with | in it")
        stage("steps", line, staged)
        with pytest.raises(AssertionError) as refusal:
            registry.step_rows()
        assert_refusal_names(str(refusal.value), "steps.md", number, line, staged)


def line_number_of(source, line: str) -> int:
    """Return *line*'s 1-based number in *source*, counted with ``split``.

    Read BEFORE staging (the fixture re-points *source* at the mutated copy)
    and counted with ``split("\\n")`` rather than the producer's
    ``splitlines()``, so the two sides of the equality share no producer.
    """
    return source.read_text().split("\n").index(line) + 1


def assert_refusal_names(message, name, number, line, staged):
    """Assert a refusal names the file, the line number, the cells and the widths.

    Args:
        message: The AssertionError's text.
        name: The registry's file name.
        number: The staged row's 1-based line number (unchanged by staging,
            which replaces one line with one line).
        line: The unstaged row, verbatim -- its width is the header's, since
            it is a live row of that table.
        staged: The row as staged, whose cell count is what the refusal
            must report.
    """
    prefix = " | ".join(cells(line)[:2])
    assert f"{name} line {number}:" in message, message
    assert f"starting {prefix!r}" in message, message
    assert (
        f"{len(cells(staged))} cells under a {len(cells(line))}-column header"
        in message
    ), message
    assert "`\\|`" in message, message


def pipe_line_violations(text: str, name: str) -> list[str]:
    """Grade EVERY ``|``-leading line of a registry against the table it sits in.

    The developer's ruling of 2026-09-14, taken at its word: every line
    starting with ``|`` in steps, ledger and rulings splits (respecting
    ``\\|``) to exactly its header's width, named by file and row.  This is
    wider than :func:`_tables.rows_under`, which reads only the four tables
    the gate consumes: ``steps.md``'s two preamble tables, every separator
    row and a ``|`` line NO table holds are graded here and nowhere else.

    A table is what GFM says it is: a ``|`` line followed by a separator row
    (cells of ``-`` and ``:`` only), then every ``|`` line until a line that
    is not one.  A ``|`` line whose next line is not a separator is not a
    table at all -- GFM renders it as prose -- which is how a row parted from
    its table by a blank line in a hand-resolved merge would vanish.

    Args:
        text: The whole document.
        name: The document's file name, for the messages.

    Returns:
        One message per offending line, empty when the document is clean.
    """
    lines = text.split("\n")
    problems: list[str] = []
    i = 0
    while i < len(lines):
        header = cells(lines[i])
        if header is None:
            i += 1
            continue
        separator = cells(lines[i + 1]) if i + 1 < len(lines) else None
        if separator is None or not all(
            c and set(c) <= {"-", ":"} for c in separator
        ):
            problems.append(
                f"{name} line {i + 1}: a `|` line no table holds "
                f"({lines[i][:60]!r}) -- GFM needs a separator row under a "
                f"header, so this renders as prose and no reader sees a row"
            )
            i += 1
            continue
        j = i + 1
        while j < len(lines) and (row := cells(lines[j])) is not None:
            if len(row) != len(header):
                problems.append(
                    f"{name} line {j + 1}: the row starting "
                    f"{' | '.join(row[:2])!r} splits to {len(row)} cells "
                    f"under a {len(header)}-column header"
                )
            j += 1
        i = j
    return problems


class TestEveryPipeLineIsARowOfItsTablesWidth:
    """The ruling's literal predicate, over the live files and two staged shapes."""

    @pytest.mark.parametrize("which", ["steps", "ledger", "rulings"])
    def test_the_live_registry_holds_no_stray_or_mis_width_pipe_line(self, which):
        """Every ``|`` line of the real file sits in a table at its width."""
        source = {
            "steps": registry.STEPS, "ledger": registry.LEDGER,
            "rulings": rulings.RULINGS,
        }[which]
        problems = pipe_line_violations(source.read_text(), source.name)
        assert not problems, problems

    def test_a_row_parted_from_its_table_by_a_blank_line_is_named(self, stage):
        """A blank line before a live ledger row makes it a ``|`` line no table holds.

        And every row AFTER it too: GFM ends the table at the blank line, and
        a ``|`` line with no separator under it is a paragraph, so the whole
        tail of the ledger stops being rows.  The first message names the
        parted row; the rest name its successors, each for the same reason.
        """
        line = row_of("ledger", a_live_ledger_row())
        number = line_number_of(registry.LEDGER, line)
        stage("ledger", "\n" + line, "\n\n" + line)
        problems = pipe_line_violations(registry.LEDGER.read_text(), "ledger.md")
        assert problems, "the parted row must be named"
        assert problems[0].startswith(
            f"ledger.md line {number + 1}: a `|` line no table holds"
        ), problems[0]
        assert all("no table holds" in p for p in problems), problems

    def test_a_table_the_gate_never_reads_is_graded_too(self, stage):
        """``steps.md``'s preamble table is outside every producer, not outside the ruling."""
        separator = "| column | what it holds |\n|---|---|\n"
        stage("steps", separator, separator + "| stray | a | b |\n")
        text = registry.STEPS.read_text()
        number = text.split("\n").index("| stray | a | b |") + 1
        problems = pipe_line_violations(text, "steps.md")
        assert problems == [
            f"steps.md line {number}: the row starting 'stray | a' splits to 3 "
            f"cells under a 2-column header"
        ], problems
