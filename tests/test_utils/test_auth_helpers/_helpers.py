"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_utils/test_auth_helpers.py``, moved verbatim by ``tools/split``.
"""

import logging

from app.models.ref import Status


class _AuthHelpersLogCapture:
    """Context manager that captures records on the auth_helpers logger.

    The helpers emit on ``app.utils.auth_helpers``; capturing on that
    specific logger keeps the assertion targeted -- a new event from
    an unrelated module cannot accidentally satisfy the test.
    """

    def __init__(self, level=logging.DEBUG):
        self._logger = logging.getLogger("app.utils.auth_helpers")
        self._level = level
        self.records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self.records.append(record)
        self._prior_level = None
        self._prior_propagate = None

    def __enter__(self):
        self._prior_level = self._logger.level
        self._prior_propagate = self._logger.propagate
        self._logger.addHandler(self._handler)
        self._logger.setLevel(self._level)
        # Disable propagation so the captured records do not also fire
        # on parent loggers (the project's root JSON handler would
        # spam stdout during the test).
        self._logger.propagate = False
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prior_level)
        self._logger.propagate = self._prior_propagate

    def find(self, event_name):
        """Return the first record whose ``event`` field matches *event_name*."""
        for record in self.records:
            if getattr(record, "event", None) == event_name:
                return record
        return None
