"""Architecture test: the CALENDAR-WIDE paycheck projection has ONE spelling.

Plan step **salary:R14-a**, closing ledger row **N-443**.  Running the paycheck
engine over an owner's WHOLE saved calendar, with tax configs resolved per
period year, was written out longhand in three places --
``income_service.SalaryPricing._breakdown_by_period``,
``routes/salary/views.py`` and ``routes/salary/cockpit.py`` -- each pairing
:func:`~app.services.tax_config_service.load_tax_configs_for_periods` with
:func:`~app.services.paycheck_calculator.project_salary` over the same
``calendar.saved()``.  Ruling **R-IZ**: a second walk is a cache with no
column, agreement is not the test, and where a layer puts the shared leaf out
of reach the remedy is to MOVE THE LEAF.  The leaf is
:func:`app.services.income_service.project_profile`.

What this test enforces
-----------------------

For every ``.py`` file under ``app/``: exactly ONE ``ast.Call`` to
``project_salary`` that passes the keyword ``configs_by_year``, and it is in
``app/services/income_service.py``.

Why THAT predicate and not "one caller of ``project_salary``"
-------------------------------------------------------------

``project_salary`` takes exactly one of two tax-config sources, and they are
two different questions rather than two spellings of one:

* ``configs_by_year=`` -- a ``{tax_year: configs}`` mapping, which is what a
  MULTI-YEAR horizon over the owner's whole calendar needs.  This is N-443's
  subject and the thing that must have one spelling.
* ``tax_configs=`` -- ONE config set for ONE tax year, correct over a year
  SLICE.  ``tax_withholding_service`` prices a year's remainder that way and
  ``tax_report_service`` sums one tax year's pre-tax total that way.

Both of those were checked by hand when this test was written (2026-09-03) and
neither is a calendar-wide walk, so asserting "one caller of ``project_salary``"
would have been a FALSE rule that fires on two correct sites.  A census is only
as good as its predicate, and this one is the narrow true predicate rather than
the wide convenient one.

Why AST, not grep
-----------------

``project_salary`` appears in prose -- docstrings and ``#`` comments -- in
ten ``app/`` modules after this change (eleven before it, cockpit.py having
dropped its mention), so a text search answers overwhelmingly with the
documentation rather than with the callers.  Walking ``ast.Call`` sees only
invocation, and reading the call's KEYWORDS is what separates the two modes
above -- which a grep cannot do at all.

What this census CANNOT see
---------------------------

Stated because an unstated limit reads as no limit.
:func:`test_the_blind_spots_are_the_ones_named` pins the FIRST FOUR of the
five below, which are call shapes it can parse; the fifth is a different
code shape entirely and no assertion over this scanner can pin it.  Each of
the four was measured against this scanner:

* ``project_salary(b, p, **{"configs_by_year": c})`` and
  ``project_salary(b, p, **kw)`` -- a ``**`` unpacking is an
  ``ast.keyword`` whose ``arg`` is ``None``, so the keyword test cannot see
  the name.
* ``f = paycheck_calculator.project_salary`` then ``f(...)`` -- an assignment
  alias, where :func:`_local_names` reads only ``ImportFrom``.
* ``getattr(pc, "project_salary")(...)``.
* NOT PINNED, and unpinnable here: a hand-rolled
  ``load_tax_configs_for_periods`` plus a per-period
  :func:`~app.services.paycheck_calculator.calculate_paycheck` loop.  It
  calls ``project_salary`` nowhere, so this census cannot see it by
  construction -- and it is the shape a future author is MOST likely to
  write, which is why it is named here rather than left implicit.

None of these appears in ``app/`` today.  They are the shapes a reviewer must
still catch by eye; this test is a floor, not a ceiling.  The census also
reads only ``app/`` -- ``scripts/`` and ``tools/`` are clean, and
``tests/test_services/test_paycheck_calculator.py`` holds two legitimate
calls that are deliberately out of scope.

The negative case
-----------------

:func:`test_the_scanner_fires_on_a_planted_second_spelling` plants the
violation as source and asserts the scanner finds it, and plants the
year-slice form to prove it does NOT.  A census that
returns "no violations" is indistinguishable from a census that looked in the
wrong place; this repo has measured that failure many separate ways, so the
passing claim is worth exactly what the firing proof is.
"""

import ast
from pathlib import Path


#: The engine entry whose calendar-wide use is being constrained.
_PROJECTION = "project_salary"

#: The keyword that marks the MULTI-YEAR, whole-calendar mode.  Its presence
#: is what makes a call an instance of N-443's rule; a call passing
#: ``tax_configs`` instead is the year-slice mode and is not in scope.
_CALENDAR_WIDE_KEYWORD = "configs_by_year"

#: The ONE module allowed to spell it, relative to the repo root.
_THE_LEAF = "app/services/income_service.py"

#: Where the engine itself defines the function.  Its ``def`` is not a call,
#: so it never enters the census -- named here so a reader is not left
#: wondering whether the scanner is simply blind to it.
_THE_ENGINE = "app/services/paycheck_calculator.py"


def _repo_root() -> Path:
    """Return the repository root, from this file's known depth."""
    return Path(__file__).resolve().parents[2]


def _local_names(tree: ast.AST) -> set[str]:
    """Return every local name in *tree* bound to the engine's projection.

    ``project_salary`` itself, plus whatever an ``import ... as`` bound it to.
    Resolved because the ALIAS is what appears at the call site: a census that
    matched the imported name alone would be blind to exactly the module that
    renamed it, and "no violations" would then be indistinguishable from "did
    not look".

    Args:
        tree: The parsed module.

    Returns:
        The set of names that call the projection in this module.
    """
    names = {_PROJECTION}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _PROJECTION and alias.asname:
                    names.add(alias.asname)
    return names


def _calendar_wide_calls(tree: ast.AST) -> int:
    """Return how many calendar-wide ``project_salary`` calls *tree* holds.

    A call counts when its callee resolves to the projection -- a bare name
    (including an ``import ... as`` alias, see :func:`_local_names`) or any
    attribute access ending in ``project_salary``, which is the
    ``paycheck_calculator.project_salary(...)`` form -- AND it passes
    ``configs_by_year`` by keyword.

    Args:
        tree: The parsed module.

    Returns:
        The number of matching calls.
    """
    names = _local_names(tree)
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            matches = func.attr == _PROJECTION
        elif isinstance(func, ast.Name):
            matches = func.id in names
        else:
            matches = False
        if not matches:
            continue
        if any(kw.arg == _CALENDAR_WIDE_KEYWORD for kw in node.keywords):
            found += 1
    return found


def _census(root: Path) -> dict[str, int]:
    """Return ``{relative path: count}`` for every app module with a match."""
    app_dir = root / "app"
    counts: dict[str, int] = {}
    for path in sorted(app_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _calendar_wide_calls(tree)
        if hits:
            counts[str(path.relative_to(root))] = hits
    return counts


def test_the_calendar_wide_projection_is_spelled_once():
    """Only ``income_service`` runs the engine over the whole calendar.

    The rule N-443 exists to make structural.  Asserted as the WHOLE census
    rather than as "the other two sites are clean", so a FOURTH spelling
    appearing in a module nobody thought to name fails this test too.
    """
    census = _census(_repo_root())
    assert census == {_THE_LEAF: 1}, (
        "The calendar-wide paycheck projection must be spelled exactly once, "
        f"in {_THE_LEAF} (income_service.project_profile). Census: {census}. "
        "A new entry here is ledger row N-443 recurring: route it through "
        "income_service.project_profile instead of pairing "
        "load_tax_configs_for_periods with project_salary again."
    )


def test_the_engine_module_defines_it_without_calling_it():
    """The engine's own module is not a hidden member of the census.

    ``project_salary`` is DEFINED in ``paycheck_calculator``; a ``def`` is not
    an ``ast.Call``, so the module contributes nothing above.  Stated as a test
    rather than a comment because "the scanner does not see the engine" and
    "the engine does not call it" look identical from the census alone.
    """
    root = _repo_root()
    tree = ast.parse(
        (root / _THE_ENGINE).read_text(encoding="utf-8"), filename=_THE_ENGINE,
    )
    assert _calendar_wide_calls(tree) == 0


def test_the_scanner_fires_on_a_planted_second_spelling():
    """The census FINDS a planted calendar-wide call, and ignores a slice one.

    Two plants rather than one, because this scanner's predicate has two
    halves and a test of only the first would pass while the second was
    inverted: the keyword arm must ACCEPT ``configs_by_year`` and REJECT
    ``tax_configs``.  Without the second plant a scanner that counted every
    ``project_salary`` call would look identical here and would fail the real
    census against the two legitimate year-slice callers.
    """
    calendar_wide = ast.parse(
        "paycheck_calculator.project_salary(\n"
        "    PayrollBasis(profile, calendar), periods,\n"
        "    configs_by_year=configs, calibration=profile.calibration,\n"
        ")\n"
    )
    assert _calendar_wide_calls(calendar_wide) == 1

    year_slice = ast.parse(
        "paycheck_calculator.project_salary(\n"
        "    basis, remainder, tax_configs, calibration=cal,\n"
        ")\n"
    )
    assert _calendar_wide_calls(year_slice) == 0


def test_the_scanner_sees_an_aliased_import():
    """An ``import ... as`` alias cannot hide a second spelling.

    :func:`_local_names` resolves the binding, so a module that renames the
    engine's entry is still counted -- the evasion an identifier-exact grep
    misses, and the one a name-only AST census misses too.  Both the aliased
    and the direct import are asserted, because a scanner that had simply
    stopped filtering on the name would also pass the aliased case.
    """
    aliased = ast.parse(
        "from app.services.paycheck_calculator import project_salary as ps\n"
        "ps(basis, periods, configs_by_year=configs)\n"
    )
    assert _calendar_wide_calls(aliased) == 1

    direct = ast.parse(
        "from app.services.paycheck_calculator import project_salary\n"
        "project_salary(basis, periods, configs_by_year=configs)\n"
    )
    assert _calendar_wide_calls(direct) == 1

    # A bare name that no import bound to the engine is NOT counted, where an
    # ATTRIBUTE ending in the name is -- the two arms are deliberately
    # asymmetric, so this pins each rather than leaving a reader to infer the
    # direction of the scanner's imprecision.
    unbound_bare_name = ast.parse(
        "ps(basis, periods, configs_by_year=configs)\n"
    )
    assert _calendar_wide_calls(unbound_bare_name) == 0

    any_attribute = ast.parse(
        "anything.project_salary(basis, periods, configs_by_year=configs)\n"
    )
    assert _calendar_wide_calls(any_attribute) == 1


def test_the_blind_spots_are_the_ones_named():
    """Each documented blind spot really is blind, and no other one is claimed.

    The module docstring lists four call shapes this census cannot see.  A
    list like that is worth nothing unless it is executable: an unstated limit
    reads as no limit, and a STATED limit that has quietly been fixed sends
    the next reviewer hunting for a hole that is not there.  Both directions
    fail here.
    """
    blind = {
        "kwargs_literal":
            "project_salary(b, p, **{'configs_by_year': c})\n",
        "kwargs_variable":
            "project_salary(b, p, **kw)\n",
        "assignment_alias":
            "f = paycheck_calculator.project_salary\n"
            "f(b, p, configs_by_year=c)\n",
        "getattr_form":
            "getattr(pc, 'project_salary')(b, p, configs_by_year=c)\n",
    }
    for label, source in blind.items():
        assert _calendar_wide_calls(ast.parse(source)) == 0, (
            f"{label} is no longer a blind spot -- the scanner now sees it. "
            "That is an improvement, but the module docstring still lists it "
            "as unseen: delete that entry."
        )
