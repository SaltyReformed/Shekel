"""How a test ended, named from its phases' reports (:func:`tools.testrecord.record.verdict`).

The wrapper run's stopping under an exception is graded through the door that uses it,
:func:`tools.split._run.observe` (``tools/split/test_run.py``), against a stand-in for
``scripts/test.sh``.
"""
from __future__ import annotations

import pytest

from tools.testrecord.record import verdict


class TestTheVerdict:
    """Each combination of phase reports names one outcome."""

    @pytest.mark.parametrize("phases, status", [
        ({"setup": ("passed", False), "call": ("passed", False)}, "passed"),
        ({"setup": ("passed", False), "call": ("failed", False)}, "failed"),
        ({"setup": ("failed", False)}, "error"),
        ({"setup": ("skipped", False)}, "skipped"),
        ({"setup": ("skipped", True)}, "xfailed"),
        ({"setup": ("passed", False), "call": ("skipped", True)}, "xfailed"),
        ({"setup": ("passed", False), "call": ("passed", True)}, "xpassed"),
        ({"setup": ("passed", False), "call": ("passed", False),
          "teardown": ("failed", False)}, "passed+teardown-error"),
    ])
    def test_verdict(self, phases, status):
        """Each phase combination names its outcome."""
        assert verdict(phases) == status
