"""Architecture test: an inline style attribute is refused by a real gate.

Plan step **bank_import:X-gi**, finding **bank_import:N-405**.

What the property is
--------------------

The app serves ``Content-Security-Policy: style-src 'self'`` with no
``unsafe-inline`` (``app/__init__.py``), so a browser REFUSES a ``style="..."``
attribute outright and the declaration silently never applies.  A rule that
never fires and a rule that was never needed look identical on screen, which is
why this is a gate rather than a convention: ``SHK01`` in
``.djlint_rules.yaml``, run by pre-commit and by CI's own
``djlint app/templates/ --lint``.

Why this test exists, and what it caught
----------------------------------------

**The gate the convention NAMED could not see the codebase's own markup.**
``app/static/css/accounts.css`` cites djlint's built-in ``H021`` three times as
the thing that refuses an inline style.  Measured 2026-09-04 on the pinned
djlint 1.39.2 under this repo's ``jinja`` profile: H021 refuses an inline style
UNLESS a Jinja expression sits between ``style=`` and the tag's own ``>``.  The
merchants page's search box carried two (``maxlength`` and ``value``), so
``style="max-width: 22rem;"`` shipped through pre-commit AND CI, green over all
159 templates, and the box never had that width in a browser for as long as it
existed.  That is finding **N-405**.

**And nothing would have noticed SHK01 disappearing.**  A rules file is data,
not code: delete it, rename it, or take a djlint upgrade that drops YAML custom
rules, and every gate in the project stays green while the class it closed
ships again.  The precedent this follows is
``tools/pylint/shekel_checkers/``, which ships with its own tests for the same
reason.

**The fixture is written INSIDE the repository, and that is load-bearing.**
djlint resolves its project root by walking UP from the path it is given, and
reads ``.djlint_rules.yaml`` from there -- so a fixture in ``/tmp`` loads no
custom rules at all and the assertion below would pass against a deleted rules
file.  An earlier draft of this measurement did exactly that and reported every
shape clean, including shapes that had just been measured firing.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

#: The repository root -- ``tests/test_arch/<this file>`` is three levels down.
#: Resolved from ``__file__`` rather than from the working directory, because
#: the value has to be the tree the test itself lives in: several worktrees of
#: this project run suites at once, and a cwd-derived root can name another
#: lane's checkout and grade its rules file instead of this one's.
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The shape N-405 shipped: a style attribute with a Jinja expression AFTER it,
#: which is the one form djlint's own H021 does not match.
_BLIND_TO_H021 = '<input class="a" style="max-width: 22rem;" value="{{ x }}">\n'

#: The same control with the width in a class, which is the remedy.
_CLEAN = '<input class="a stmt-merchant-search" value="{{ x }}">\n'


def _lint(markup: str) -> subprocess.CompletedProcess:
    """Return djlint's result over *markup*, linted as a file in this repo.

    The file is created under the repository root and removed again, so
    djlint's project-root walk finds this tree's ``.djlint_rules.yaml``.  The
    name carries a uuid because peer worktrees and xdist workers run
    concurrently and a fixed name is a collision.

    Args:
        markup: The template body to lint.

    Returns:
        The finished ``djlint --lint`` process.
    """
    fixture = _REPO_ROOT / f".djlint-fixture-{uuid.uuid4().hex}.html"
    fixture.write_text(markup, encoding="utf-8")
    try:
        return subprocess.run(
            [sys.executable, "-m", "djlint", str(fixture), "--lint"],
            capture_output=True, text=True, cwd=_REPO_ROOT, check=False,
        )
    finally:
        fixture.unlink(missing_ok=True)


class TestTheInlineStyleGateExistsAndFires:
    """``SHK01`` is present, refuses the CSP-dead shape, and passes the remedy."""

    def test_the_rules_file_is_present_and_declares_SHK01(self):
        """The gate is DATA, so its absence is the failure mode to assert.

        A missing file makes every other assertion in this module vacuous:
        djlint would report nothing and exit 0 for the same reason it does on
        clean markup.
        """
        rules = _REPO_ROOT / ".djlint_rules.yaml"

        assert rules.is_file(), (
            f"{rules} is missing: the inline-style gate is a rules FILE, and "
            "without it djlint exits 0 on markup the browser refuses."
        )
        assert "SHK01" in rules.read_text(encoding="utf-8")

    def test_it_REFUSES_the_shape_H021_is_blind_to(self):
        """The N-405 shape must fail the gate, exit code and all.

        Asserted on the exit STATUS as well as the output, because CI's step
        fails the build on the status and a rule that reported without exiting
        non-zero would block nothing.
        """
        result = _lint(_BLIND_TO_H021)

        assert result.returncode != 0, (
            "djlint exited 0 on an inline style attribute. CI fails the build "
            f"on this exit code, so the gate blocks nothing.\n{result.stdout}"
        )
        assert "SHK01" in result.stdout, (
            "djlint refused the markup but not under SHK01, so this test is "
            f"grading some other rule:\n{result.stdout}"
        )

    def test_a_style_in_a_CLASS_passes(self):
        """The remedy must pass, or the gate refuses its own fix.

        The other direction of the same control: a test that only ever asserts
        a refusal passes against a rule that refuses everything.
        """
        result = _lint(_CLEAN)

        assert result.returncode == 0, (
            f"djlint refused the REMEDY for N-405:\n{result.stdout}"
        )
        assert "SHK01" not in result.stdout

    @pytest.mark.parametrize(
        ("label", "markup"),
        [
            # A ``>`` inside an earlier attribute value, which is live on 23
            # tags in app/templates today as the ``{% if x > 0 %}`` idiom in a
            # class. A pattern stopping at the first ``>`` misses all of them.
            ("a > inside an earlier attribute value",
             '<a title="a > b" style="color: red;">x</a>\n'),
            ("a Jinja comparison in an earlier attribute",
             '<div class="{{ \'a\' if n > 3 else \'b\' }}" '
             'style="max-width: 1rem;">x</div>\n'),
            ("whitespace around the equals sign",
             '<div class="c" style = "color: red;">x</div>\n'),
            ("single quotes", "<div class='c' style='color: red;'>x</div>\n"),
            ("an uppercase tag and attribute",
             '<DIV CLASS="c" STYLE="color: red;">x</DIV>\n'),
            ("a tag broken across lines",
             '<input class="a"\n       style="max-width: 1rem;"\n'
             '       value="{{ x }}">\n'),
        ],
    )
    def test_it_refuses_every_spelling_that_reaches_a_browser(
        self, label, markup,
    ):
        """Each of these is a real inline style that H021 alone lets through.

        They are parametrized rather than asserted in one body so a pattern
        change that loses ONE spelling names which, instead of failing on
        whichever happens to be first.

        Args:
            label: What the spelling is, for the failure message.
            markup: A template body carrying that spelling.
        """
        result = _lint(markup)

        assert "SHK01" in result.stdout, (
            f"SHK01 is blind to {label}, so that markup ships and the "
            f"declaration silently never applies:\n{markup}{result.stdout}"
        )

    @pytest.mark.parametrize(
        ("label", "markup"),
        [
            ("markup inside a Jinja comment",
             '{# <div style="color: red;">documented, not markup</div> #}\n'
             '<p>x</p>\n'),
            ("a stylesheet block", "<style>\n.a { color: red; }\n</style>\n"),
            ("a data- attribute that merely contains the word",
             '<div data-style="red" class="c">x</div>\n'),
            ("the word inside a script string",
             '<script>\n  el.setAttribute("style", "color:red");\n</script>\n'),
            ("markup inside an HTML comment",
             '<!-- <div style="color: red;">x</div> -->\n<p>y</p>\n'),
        ],
    )
    def test_it_refuses_NOTHING_that_is_not_an_inline_style(
        self, label, markup,
    ):
        """A gate that fires on documentation gets disabled, which is worse.

        The Jinja-comment case is not hypothetical: ``_grid_row_macros.html``
        documents this very rule by writing the attribute inside a comment, and
        a pattern that flagged it would make the gate unusable in the one file
        that explains it.

        Args:
            label: What the non-violation is, for the failure message.
            markup: A template body carrying it.
        """
        result = _lint(markup)

        assert "SHK01" not in result.stdout, (
            f"SHK01 fired on {label}, which is not an inline style at all:\n"
            f"{markup}{result.stdout}"
        )
