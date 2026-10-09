"""The four registry readers X-cx's migration calls read the TEXT they are handed.

X-cx's migration (piece L7, ``tools/quill``) reads each registry as a COMMIT holds it, so
a card never quotes text its commit lacks (ruling ``balance:R-BAL257``): it passes
``text=`` to :func:`_registry.step_rows`, :func:`_registry.ledger_rows`,
:func:`_rulings.ruling_rows` and :func:`_order.outcome_scopes`, and None reads the file
as every other caller does.

Each test points the reader's file at a path that does not exist, so a reader that read
its file rather than ``text`` raises, and hands it the live text with its first row
taken out, so the answer must be that text's and no file's.  A reader that ignored
``text`` fails both ways.
"""
from __future__ import annotations

from dataclasses import astuple

from tools.plan_gate import _order as order
from tools.plan_gate import _registry as registry
from tools.plan_gate import _rulings as rulings
from tools.plan_gate._tables import cells


def _without_line(text: str, wanted) -> str:
    """``text`` with its first table row whose cells ``wanted`` accepts taken out."""
    lines = text.splitlines(keepends=True)
    index = next(index for index, line in enumerate(lines)
                 if (found := cells(line)) is not None and wanted(found))
    return "".join(lines[:index] + lines[index + 1:])


def _given(module, name: str, wanted, tmp_path, monkeypatch) -> str:
    """The live text of ``module.<name>``'s file with the row ``wanted`` accepts taken
    out, the module re-pointed at a file that does not exist."""
    text = getattr(module, name).read_text()
    monkeypatch.setattr(module, name, tmp_path / "absent.md")
    return _without_line(text, wanted)


def test_step_rows_reads_the_text_it_is_given(tmp_path, monkeypatch):
    """The steps' first row taken out of the text: the rest, from the text alone."""
    rows = registry.step_rows()
    text = _given(registry, "STEPS", lambda found: found == list(astuple(rows[0])), tmp_path,
                  monkeypatch)
    assert registry.step_rows(text=text) == rows[1:]


def test_ledger_rows_reads_the_text_it_is_given(tmp_path, monkeypatch):
    """The ledger's first row taken out of the text: the rest, from the text alone."""
    rows = registry.ledger_rows()
    text = _given(registry, "LEDGER", lambda found: found == list(astuple(rows[0])), tmp_path,
                  monkeypatch)
    assert registry.ledger_rows(text=text) == rows[1:]


def test_ruling_rows_reads_the_text_it_is_given(tmp_path, monkeypatch):
    """The rulings' first row taken out of the text: the rest, from the text alone."""
    rows = rulings.ruling_rows()
    text = _given(rulings, "RULINGS", lambda found: found == list(astuple(rows[0])), tmp_path,
                  monkeypatch)
    assert rulings.ruling_rows(text=text) == rows[1:]


def test_outcome_scopes_reads_the_text_it_is_given(tmp_path, monkeypatch):
    """The OUTCOMES table's first row taken out of ``steps.md``'s text: the rest, from the
    text alone (the reader reads ``steps.md`` through :mod:`_registry`)."""
    scopes = order.outcome_scopes()
    text = _given(registry, "STEPS",
                  lambda found: len(found) == 2 and found[0] == scopes[0][0], tmp_path,
                  monkeypatch)
    assert order.outcome_scopes(text=text) == scopes[1:]
