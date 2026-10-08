"""Architecture GUARD: no request integer in ``app/`` is parsed by a bare ``int``.

Plan step balance:X-dj.  psycopg 3's SQLAlchemy dialect binds every
``Integer`` with a server-side cast (``%(id_1)s::INTEGER``), so an integer
outside a PostgreSQL ``integer``'s range that reaches a query is refused by
the cast -- an unhandled 500 -- where psycopg2 simply matched no row.  Every
integer query arg is therefore read through
:func:`app.utils.digit_strings.integer_arg`, which is ``int()`` held to that
range, and Werkzeug answers the site's default for anything outside it.

**This is a GUARD, and it says so because a guard is scaffolding**, not the
design: it exists only because ``request.args.get(..., type=int)`` stays
writable and nothing else stops the next site from writing it.  Plan step
X-ah's per-site schemas (finding N-142) would parse each query arg by what it
MEANS -- an id, a count, a year -- and make this check unnecessary; delete it
then.

What this test enforces
-----------------------

For every Python file under ``app/``: no call passes ``int`` as a Werkzeug
``MultiDict`` coercion -- as the ``type=`` keyword (every site this
application has written), or in the argument's positional slot, the third of
``get(key, default, type)`` and the second of ``getlist(key, type)``.

**What it does not see, stated rather than implied**: an ``int(...)`` applied
to a request value by hand, and a ``type`` bound to a variable that holds
``int``.  A grep of ``app/routes``, ``app/schemas`` and ``app/utils`` on
2026-10-05 found two hand-applied ``int`` calls on a submitted value, and
both keep the integer in Python, so no bind cast can refuse it:
``app/routes/debt_strategy.py``'s custom order (account ids compared with
the owner's debt accounts already loaded) and
``app/schemas/validation/_helpers.py``'s sort key for a submitted field key.
``app/services`` was not searched.  The form and path doors read ids through
:func:`~app.utils.digit_strings.parse_row_id`.

Why AST, not grep
-----------------

The token appears in prose: docstrings that explain the rule name
``type=int``, and a grep would flag the sentence that states it.
"""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"


#: Werkzeug's ``MultiDict`` methods that take a ``type`` coercion, mapped to
#: that argument's POSITIONAL index: ``get(key, default, type)`` and
#: ``getlist(key, type)``.
_TYPE_POSITION = {"get": 2, "getlist": 1}


def _is_int(node: ast.expr) -> bool:
    """Return whether *node* is the bare name ``int``."""
    return isinstance(node, ast.Name) and node.id == "int"


def _bare_int_coercions(source: str, filename: str) -> list[str]:
    """Return one message per ``int`` passed as a ``MultiDict`` coercion in *source*.

    Args:
        source: The module's text.
        filename: Named in each message so a failure points at the file.

    Returns:
        Empty when the module passes ``int`` as a coercion nowhere; otherwise
        one line per site, with its line number.
    """
    sites = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        sites.extend(
            f"{filename}:{keyword.value.lineno}: type=int"
            for keyword in node.keywords
            if keyword.arg == "type" and _is_int(keyword.value)
        )
        position = (
            _TYPE_POSITION.get(node.func.attr)
            if isinstance(node.func, ast.Attribute) else None
        )
        if (
            position is not None and len(node.args) > position
            and _is_int(node.args[position])
        ):
            sites.append(
                f"{filename}:{node.args[position].lineno}: "
                f"{node.func.attr}(..., int)",
            )
    return sites


def test_no_request_integer_is_parsed_by_a_bare_int():
    """Every integer query arg reads through ``integer_arg``.

    A failure names each site; the remedy is ``type=integer_arg``.
    """
    files = sorted(APP_DIR.rglob("*.py"))
    assert files, f"no Python files under {APP_DIR}"
    sites = [
        site
        for path in files
        for site in _bare_int_coercions(
            path.read_text(encoding="utf-8"), str(path.relative_to(APP_DIR.parent)),
        )
    ]
    assert not sites, (
        "request integers parsed by a bare int, which an out-of-range value "
        "turns into a 500 at the query's bind cast -- use "
        "app.utils.digit_strings.integer_arg:\n" + "\n".join(sites)
    )


def test_the_scanner_sees_the_spelling_it_refuses():
    """The scanner is not blind: it finds each spelling of the coercion and only those.

    Without this the guard could pass by matching nothing.  The positional
    lines pin the slot: ``int`` as ``get``'s DEFAULT (second) argument, or as
    a call's argument outside the type slot, is not a coercion.
    """
    source = (
        "a = request.args.get('x', type=int)\n"
        "b = request.args.get('y', type=integer_arg)\n"
        "c = request.args.get('z', default=0, type=int)\n"
        "# request.args.get('w', type=int) in a comment\n"
        "d = '''request.args.get('v', type=int) in a string'''\n"
        "e = request.args.get('u', 0, int)\n"
        "f = request.form.getlist('t', int)\n"
        "g = request.args.get('s', int)\n"
        "h = request.args.get('r', 0, integer_arg)\n"
        "i = sorted(xs, key=int)\n"
    )
    assert _bare_int_coercions(source, "probe.py") == [
        "probe.py:1: type=int",
        "probe.py:3: type=int",
        "probe.py:6: get(..., int)",
        "probe.py:7: getlist(..., int)",
    ]
