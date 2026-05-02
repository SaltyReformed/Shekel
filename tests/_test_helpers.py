"""
Shekel Budget App -- shared test helper utilities.

Underscore-prefixed module name keeps pytest from collecting it as a
test file.  Import functions from here in test modules that need them.
"""

from datetime import date as _real_date


def freeze_today(monkeypatch, target_date, modules=None):
    """Patch ``date.today()`` to return ``target_date`` in the given modules.

    Production services import ``date`` at module load time
    (e.g. ``from datetime import date``), so monkeypatching the global
    ``datetime.date`` does not affect them.  Each module that uses
    ``date.today()`` must be patched individually.  This helper hides
    that boilerplate.

    Args:
        monkeypatch:
            pytest's ``monkeypatch`` fixture.  Required so the patch is
            torn down at end of test automatically.
        target_date:
            The ``datetime.date`` instance that ``date.today()`` should
            return inside the patched modules.
        modules:
            Iterable of dotted module paths whose ``date`` symbol to
            replace.  Defaults to
            ``("app.services.pay_period_service",)`` because that is
            the most common patch site (``get_current_period`` reads
            ``date.today()`` when called without an explicit ``as_of``).
            Pass extras for tests that exercise other modules importing
            ``date`` directly (e.g.
            ``"app.services.dashboard_service"``).
    """
    if modules is None:
        modules = ("app.services.pay_period_service",)

    class _FrozenDate(_real_date):
        """Date subclass with a fixed ``today()`` for test isolation."""

        @classmethod
        def today(cls):
            """Return the frozen date instead of the wall-clock date."""
            return target_date

    for module_path in modules:
        monkeypatch.setattr(f"{module_path}.date", _FrozenDate)
