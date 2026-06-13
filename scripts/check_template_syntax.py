#!/usr/bin/env python3
"""Pre-render Jinja2 syntax check for the Shekel template surface.

Parses every template under ``app/templates`` so a syntax error (an
unbalanced ``{% %}``, a malformed filter expression, a mismatched block)
is caught before it ever reaches a live render. This is the
zero-extra-dependency companion to the djlint structural lint adopted in
the polyglot-cleanup Phase 5 (docs/audits/polyglot-cleanup/tooling.md):
djlint checks template/HTML *style*; this checks that the template
*parses*.

The application registers no custom Jinja extensions -- only filters and
globals, which do not affect parsing -- so a plain ``jinja2.Environment``
parses identically to ``app.jinja_env`` without needing the app config or
a database connection. If a Jinja extension that adds tag syntax is ever
registered on the app, mirror it on the environment built below.

Exit code 0 when every template parses; 1 when any template has a syntax
error (each printed as ``path:line: message``).
"""
from __future__ import annotations

import sys
from pathlib import Path

from jinja2 import Environment, TemplateSyntaxError

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"


def find_templates(root: Path) -> list[Path]:
    """Return every ``.html`` template under ``root``, sorted for stable output."""
    return sorted(root.rglob("*.html"))


def check_template(env: Environment, path: Path) -> str | None:
    """Parse one template; return a ``path:line: message`` string, or None if clean."""
    source = path.read_text(encoding="utf-8")
    try:
        env.parse(source, filename=str(path))
    except TemplateSyntaxError as exc:
        return f"{path}:{exc.lineno}: {exc.message}"
    return None


def main() -> int:
    """Parse all templates, print any syntax errors, and return an exit code."""
    if not TEMPLATES_DIR.is_dir():
        print(f"template directory not found: {TEMPLATES_DIR}", file=sys.stderr)
        return 1

    # autoescape=True mirrors Flask's HTML default; it does not affect
    # parsing but keeps the environment faithful to production rendering.
    env = Environment(autoescape=True)
    templates = find_templates(TEMPLATES_DIR)

    errors: list[str] = []
    for path in templates:
        message = check_template(env, path)
        if message is not None:
            errors.append(message)

    for message in errors:
        print(message, file=sys.stderr)

    if errors:
        print(
            f"FAIL: {len(errors)} of {len(templates)} templates have syntax errors",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(templates)} templates parsed cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
