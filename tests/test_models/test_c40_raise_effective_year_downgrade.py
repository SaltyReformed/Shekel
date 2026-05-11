"""Tests for the C-40 / F-131 fix to migration ``b4c5d6e7f8a9``.

The previous downgrade body was a bare ``pass``, which silently let
``flask db downgrade`` chain past this migration while leaving the
backfilled ``effective_year`` values in place -- misleading the
operator into believing the column had been reverted.  The
replacement downgrade raises :class:`NotImplementedError` with the
manual recovery SQL and a ``system.audit_log`` hint for identifying
which rows the migration actually touched.

Tests load the migration module dynamically and call ``downgrade()``
to assert both the exception class and the actionable contents of
the message.  The migration file is loaded once at module import via
``importlib`` because ``migrations/versions`` has no ``__init__.py``.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load_migration(filename: str):
    """Load an Alembic migration file as a Python module via importlib.

    The ``migrations/versions`` directory has no ``__init__.py`` so
    standard ``import`` would fail; alembic itself loads scripts via
    importlib at runtime.  We mirror that behaviour to call the
    ``downgrade()`` function directly from tests.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M_RAISE_YEAR = _load_migration(
    "b4c5d6e7f8a9_backfill_raise_effective_year.py"
)


class TestRaiseEffectiveYearDowngrade:
    """``b4c5d6e7f8a9.downgrade()`` refuses to run automatically.

    The migration backfilled NULL ``effective_year`` values on
    recurring salary raises; reverting blindly would NULL out
    legitimate post-migration values.  The replacement downgrade
    raises NotImplementedError; these tests confirm the exception
    class and that the message carries the manual recovery SQL.
    """

    def test_downgrade_raises_not_implemented_error(self):
        """Calling downgrade() raises NotImplementedError, not pass."""
        with pytest.raises(NotImplementedError):
            _M_RAISE_YEAR.downgrade()

    def test_downgrade_message_names_the_affected_table(self):
        """Operator must see ``salary_raises`` in the error message."""
        with pytest.raises(NotImplementedError) as exc_info:
            _M_RAISE_YEAR.downgrade()
        assert "salary.salary_raises" in str(exc_info.value)

    def test_downgrade_message_includes_audit_log_recovery_hint(self):
        """Operator must see how to find the affected row ids."""
        with pytest.raises(NotImplementedError) as exc_info:
            _M_RAISE_YEAR.downgrade()
        message = str(exc_info.value)
        # Provenance hint must point at system.audit_log so the
        # operator can identify only the rows the migration touched.
        assert "system.audit_log" in message
        # And the literal UPDATE SQL must be present so the recovery
        # is a copy-paste, not a re-derivation under pressure.
        assert "UPDATE salary.salary_raises SET effective_year = NULL" in message
