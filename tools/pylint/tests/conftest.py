"""Pytest bootstrap for the Shekel pylint-checker unit tests.

Puts the plugin directory (``tools/pylint``) on ``sys.path`` so the tests can
``import shekel_checkers`` directly, matching how ``.pylintrc``'s ``init-hook``
makes the plugin importable for pylint itself.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
