"""Tests for the C-40 / F-133 fix to migration ``7abcbf372fff``.

The previous downgrade unconditionally recreated the narrower
2-column unique constraint ``(user_id, state_code)`` after dropping
the wider 3-column form ``(user_id, state_code, tax_year)``.  Any
user who created ``state_tax_configs`` rows for multiple tax_years
after the upgrade (which is the intended post-upgrade behaviour)
would cause the recreation to fail with a UniqueViolation, leaving
the table half-reverted.  The replacement downgrade raises
:class:`NotImplementedError` with a numbered 5-step manual recovery
procedure (dedupe SELECT, DELETE, DROP / ADD CONSTRAINT, DROP COLUMN).

Tests load the migration module dynamically and call ``downgrade()``
to assert both the exception class and that the message contains the
full manual recovery procedure.  The migration file is loaded once
at module import via ``importlib`` because ``migrations/versions``
has no ``__init__.py``.
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


_M_TAX_YEAR = _load_migration(
    "7abcbf372fff_add_tax_year_to_state_tax_configs.py"
)


class TestTaxYearDowngrade:
    """``7abcbf372fff.downgrade()`` refuses to run automatically.

    The narrower 2-column unique constraint
    ``(user_id, state_code)`` cannot be safely recreated against
    post-upgrade data (multiple ``state_tax_configs`` rows per state
    per user are the intended post-upgrade behaviour).  The
    replacement downgrade raises NotImplementedError with the manual
    recovery procedure.
    """

    def test_downgrade_raises_not_implemented_error(self):
        """Calling downgrade() raises NotImplementedError, not a UniqueViolation."""
        with pytest.raises(NotImplementedError):
            _M_TAX_YEAR.downgrade()

    def test_downgrade_message_names_the_affected_constraint(self):
        """Operator must see the constraint name in the error message."""
        with pytest.raises(NotImplementedError) as exc_info:
            _M_TAX_YEAR.downgrade()
        message = str(exc_info.value)
        assert "uq_state_tax_configs_user_state_year" in message
        assert "uq_state_tax_configs_user_state" in message

    def test_downgrade_message_includes_step_by_step_recovery(self):
        """Operator must see a numbered manual-revert procedure."""
        with pytest.raises(NotImplementedError) as exc_info:
            _M_TAX_YEAR.downgrade()
        message = str(exc_info.value)
        # Numbered steps so the operator can follow them in order.
        for step in ("1.", "2.", "3.", "4.", "5."):
            assert step in message, (
                f"Recovery step {step} missing from downgrade message"
            )
        # And the SELECT that finds duplicate pairs must be present so
        # the operator knows which rows to delete first.
        assert "GROUP BY user_id, state_code" in message
        assert "DROP COLUMN tax_year" in message
