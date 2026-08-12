"""Fixtures shared by the gate's two control suites.

They live here rather than in one suite and imported by the other for a
mechanical reason with a real consequence: an imported pytest fixture is an
UNUSED IMPORT to every static checker, so the alternative is a suppression
on each importing module -- and this package holds a 10.00/10 pylint floor
precisely so that a suppression is a decision rather than a habit.  A
conftest is how pytest is designed to share a fixture, and it needs no
import at all.

Both stage a defect in a COPY of a real document and re-point the module at
it, so a control exercises the same parser on the same shapes the live files
use.  A synthetic fixture would prove only that the parser reads what it
wrote.
"""
from __future__ import annotations

import pytest

import _registry as registry


@pytest.fixture(name="stage")
def _stage(tmp_path, monkeypatch):
    """Return a helper that mutates a registry copy and re-points the module."""

    def _apply(which: str, old: str, new: str) -> None:
        source = {"ledger": registry.LEDGER, "steps": registry.STEPS}[which]
        text = source.read_text()
        assert old in text, f"control anchor {old!r} is not in the real {which}"
        target = tmp_path / source.name
        target.write_text(text.replace(old, new, 1))
        monkeypatch.setattr(registry, which.upper(), target)

    return _apply


@pytest.fixture(name="stage_arc")
def _stage_arc(tmp_path, monkeypatch):
    """Return a helper that mutates an ARC DOCUMENT copy and re-points the map.

    Separate from ``stage`` because ``ARC_DOCS`` is a dict rather than a module
    attribute, and because the defects it plants are in the SPECIFICATIONS
    rather than in a registry table.
    """

    def _apply(arc: str, old: str, new: str) -> None:
        source = registry.ARC_DOCS[arc]
        text = source.read_text()
        assert old in text, f"control anchor {old!r} is not in the real {arc} doc"
        target = tmp_path / source.name
        target.write_text(text.replace(old, new, 1))
        monkeypatch.setitem(registry.ARC_DOCS, arc, target)

    return _apply
