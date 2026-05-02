"""
Shekel Budget App -- test_services local conftest.

Module-level autouse fixture that freezes ``date.today()`` inside
``pay_period_service`` to a value within the seed_periods range.
Service tests use ``seed_periods`` (calendar-anchored 2026-01-02 to
2026-05-21) and many have hardcoded calendar literals; freezing today
inside that window keeps ``get_current_period()`` deterministic
regardless of wall-clock date, without disturbing calendar-anchored
assertions.

A single test that needs a different ``today`` can override this via
its own ``monkeypatch`` -- pytest's last-applied wins.
"""

from datetime import date

import pytest

from tests._test_helpers import freeze_today


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze pay_period_service.date.today() to 2026-03-20 (mid-period 5)."""
    freeze_today(monkeypatch, date(2026, 3, 20))
