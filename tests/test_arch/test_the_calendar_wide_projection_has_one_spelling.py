"""Architecture test: the CALENDAR-WIDE paycheck projection has ONE spelling.

Plan step **salary:R14-a**, closing ledger row **N-443**.  Running the paycheck
engine over an owner's WHOLE saved calendar, with tax configs resolved per
period year, was written out longhand in three places --
``income_service.SalaryPricing``, ``routes/salary/views.py`` and
``routes/salary/cockpit.py`` -- each pairing
``tax_config_service.load_tax_configs_for_periods`` (the door plan step
salary:S3-d replaced with :func:`~app.services.tax_config_service
.configs_by_year`) with
:func:`~app.services.paycheck_calculator.project_salary` over the same
``calendar.saved()``.  Ruling **R-IZ**: a second walk is a cache with no
column, agreement is not the test, and where a layer puts the shared leaf out
of reach the remedy is to MOVE THE LEAF.  The leaf is
:class:`app.services.income_service.ProfilePaychecks`.

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
* A hand-rolled tax-config resolution plus a per-period
  :func:`~app.services.paycheck_calculator.calculate_paycheck` loop calls
  ``project_salary`` nowhere, so THIS census cannot see it by construction.
  It was named here as unpinnable until plan step **salary:S3-d**;
  :func:`test_the_direct_engine_callers_are_the_six_C12_owns` is the second
  census that pins it, over ``calculate_paycheck`` itself.

**The second census has the SAME first four blind spots**, because it uses the
same matcher: a ``**`` unpacking is irrelevant to it (it reads no keyword), but
an assignment alias, a ``getattr`` form and a name bound by anything other than
``ImportFrom`` are all invisible to it exactly as they are here.
:func:`test_the_per_period_blind_spots_are_the_ones_named` pins them, so the
statement above is executable for both censuses rather than for one.

The first four appear nowhere in ``app/`` today.  They are the shapes a
reviewer must still catch by eye; this test is a floor, not a ceiling.  The census also
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
        f"in {_THE_LEAF} (ProfilePaychecks.over). Census: {census}. "
        "A new entry here is ledger row N-443 recurring: route it through "
        "income_service.ProfilePaychecks instead of pairing "
        "a tax-config resolution with project_salary again."
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


#: The engine's PER-PERIOD entry.  The second census below is over this name,
#: where the first is over ``project_salary``: a hand-rolled
#: tax-config resolution plus a loop of these calls the first
#: census's subject nowhere, which is the blind spot the module docstring
#: named as unpinnable until plan step **salary:S3-d**.
_PER_PERIOD = "calculate_paycheck"

#: Every ``app/`` site that prices a paycheck by calling the engine's
#: per-period entry directly, as ``{relative path: call count}``.
#:
#: **These are ledger rows P62 / P63 / P64 and plan step C12, enumerated
#: rather than described.**  Each prices ONE period, resolving its tax configs
#: through a different door from :class:`~app.services.income_service
#: .ProfilePaychecks`, so routing them through the pass's pricer could move a
#: figure ``/savings`` and ``/retirement`` publish -- which is why plan step
#: salary:S3-d left them alone and why C12 is ruled to need its own decision
#: first.  On ``/retirement`` the current period is priced at
#: ``retirement_dashboard_service`` AND again inside the projection axis, so
#: that one payday is priced twice today.
#:
#: The census is by ENUMERATION and not by subtraction: every entry here was
#: read and counted, so a SEVENTH site fails this test and C12 deleting one
#: fails it too.  Both directions are the point.
_DIRECT_ENGINE_CALLERS = {
    "app/routes/salary/_helpers.py": 2,
    "app/routes/salary/cockpit.py": 1,
    "app/routes/salary/profiles.py": 1,
    "app/services/retirement_dashboard_service.py": 1,
    "app/services/savings_dashboard_service/_metrics.py": 1,
}


def _per_period_calls(tree: ast.AST) -> int:
    """Return how many direct ``calculate_paycheck`` calls *tree* holds.

    The same matcher :func:`_calendar_wide_calls` uses, over the per-period
    name and with no keyword test: there is only one mode of this call, so
    every one of them counts.

    Args:
        tree: The parsed module.

    Returns:
        The number of matching calls.
    """
    names = {_PER_PERIOD}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _PER_PERIOD and alias.asname:
                    names.add(alias.asname)
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            found += func.attr == _PER_PERIOD
        elif isinstance(func, ast.Name):
            found += func.id in names
    return found


def _per_period_census(root: Path) -> dict[str, int]:
    """Return ``{relative path: count}`` for direct engine callers under app/.

    The ENGINE's own module is excluded: ``project_salary`` calls
    ``calculate_paycheck`` once, in the loop that IS the batch entry, and
    counting the definition's own use would put the producer in a census of
    its bypassers.
    """
    counts: dict[str, int] = {}
    for path in sorted((root / "app").rglob("*.py")):
        relative = str(path.relative_to(root))
        if relative == _THE_ENGINE:
            continue
        hits = _per_period_calls(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
        )
        if hits:
            counts[relative] = hits
    return counts


def test_the_direct_engine_callers_are_the_six_C12_owns():
    """Only the six enumerated sites price a paycheck outside the pricer.

    The second census, added at plan step **salary:S3-d**, over the blind spot
    the first one names: a per-period ``calculate_paycheck`` loop is invisible
    to a ``project_salary`` scanner by construction, and it is the shape a
    future author is most likely to write.

    It asserts the WHOLE map rather than "no new file has one", so both
    directions fail: a seventh caller appearing anywhere, and one of these six
    being deleted or moved without :data:`_DIRECT_ENGINE_CALLERS` being told.
    The second is what makes this test C12's checklist rather than a fence C12
    would have to remember to take down.
    """
    census = _per_period_census(_repo_root())
    assert census == _DIRECT_ENGINE_CALLERS, (
        "The set of app/ sites calling paycheck_calculator.calculate_paycheck "
        f"directly has changed. Census: {census}; expected "
        f"{_DIRECT_ENGINE_CALLERS}. A NEW entry is a seventh place that "
        "prices a paycheck outside the read pass's "
        "income_service.PaycheckPricing -- route it through "
        "ctx.paychecks().for_profile(profile).at(period) instead, unless it "
        "genuinely needs a tax-config door of its own, in which case say so "
        "here. A MISSING entry means plan step C12 has folded one in: delete "
        "it from _DIRECT_ENGINE_CALLERS, and when the map empties delete this "
        "test with the finding it tracks (ledger rows P62 / P63 / P64)."
    )


def test_the_per_period_scanner_fires_on_a_planted_call():
    """The second census's scanner really sees the shape it claims to.

    The same firing proof :func:`test_the_scanner_fires_on_a_planted_second_spelling`
    gives the first census, and for the reason stated in the module docstring:
    a census that returns "nothing new" is indistinguishable from a census
    that looked in the wrong place until you make it fire.
    """
    attribute_form = ast.parse(
        "paycheck_calculator.calculate_paycheck(basis, period, configs)\n"
    )
    assert _per_period_calls(attribute_form) == 1

    imported_form = ast.parse(
        "from app.services.paycheck_calculator import calculate_paycheck\n"
        "calculate_paycheck(basis, period, configs)\n"
    )
    assert _per_period_calls(imported_form) == 1

    aliased_form = ast.parse(
        "from app.services.paycheck_calculator import "
        "calculate_paycheck as cp\n"
        "cp(basis, period, configs)\n"
    )
    assert _per_period_calls(aliased_form) == 1

    # The batch entry is a DIFFERENT question and must not be counted here;
    # the first census owns it.
    assert _per_period_calls(
        ast.parse("project_salary(basis, periods, configs_by_year=c)\n"),
    ) == 0


def test_the_per_period_blind_spots_are_the_ones_named():
    """The second census's documented blind spots really are blind.

    :func:`test_the_blind_spots_are_the_ones_named`'s twin, over
    ``calculate_paycheck``.  The module docstring says the two censuses share
    their limits; a shared limit stated once and executed once is a claim about
    one of them, which is how the pair would come apart.  Both directions fail
    here, as they do for the first census.
    """
    blind = {
        "assignment_alias":
            "f = paycheck_calculator.calculate_paycheck\n"
            "f(basis, period, configs)\n",
        "getattr_form":
            "getattr(pc, 'calculate_paycheck')(basis, period, configs)\n",
    }
    for label, source in blind.items():
        assert _per_period_calls(ast.parse(source)) == 0, (
            f"{label} is no longer a blind spot of the per-period census -- "
            "the scanner now sees it. That is an improvement, but the module "
            "docstring still lists it as unseen: delete that entry."
        )
