"""Architecture test: every timeline caller reads the boundary off the CALENDAR.

Plan step **salary:S3-e-1** (ruling **R-SAL16**).  The step moved a decision
OUT of :func:`~app.services.investment_projection.build_contribution_timeline`
-- where :meth:`AccountPayrollFeed.prices` answered it and no caller could hand
it a wrong answer -- and into the callers, as the ``saved_through`` keyword.
That is a real cost of the move and this census is what pays it.

**Two adversarial reviews of that step raised the same gap independently**: the
step's own unit cases hand ``saved_through`` in directly, so they grade the
RULE and not the WIRING.  The failure they described is a caller writing
:meth:`~app.services.pay_calendar.PayCalendar.opening_bound`, or a horizon off
a SECOND calendar, at either site -- after which every in-window period
silently gains that account's average recurring TRANSFER, for the whole
horizon, on ``/retirement``'s readiness verdict and ``/savings``'s Horizon
band, with the suite green.  It is **R-SAL16**'s own stated hazard relocated
one layer up rather than removed.

What this test enforces
-----------------------

For every ``.py`` file under ``app/``: every ``ast.Call`` to
``build_contribution_timeline`` passes ``saved_through``, and its value is a
call to something named ``horizon``.

**Why the shape of the value and not just its presence.** A required keyword
with no default already makes OMITTING it a ``TypeError`` -- that half needs no
census.  What no interpreter can catch is a caller that passes the wrong DATE,
which is the whole of the hazard above, so the predicate is on the expression.

**Why ``horizon`` by NAME rather than the full receiver chain.** The two live
sites spell it ``ctx.balance_ctx.calendar().horizon()``, but the receiver is a
different expression on each and would be a third on any new one; pinning the
chain would fail a correct site for its spelling.  ``horizon()`` is the ONE
producer of *the last day the saved schedule covers*
(:meth:`~app.services.pay_calendar.PayCalendar.horizon`), so naming the leaf is
the narrow true predicate.  It does NOT prove the calendar is the one
*periods* came from -- see the blind spots.

What this census CANNOT see
---------------------------

Stated because an unstated limit reads as no limit, and each was measured
against this scanner rather than assumed:

* **WHICH calendar.** ``other_ctx.calendar().horizon()`` passes.  Nothing
  static ties the horizon to the axis; that pairing is a precondition stated
  in the callee's ``Args`` block, and this census is the fence around it.

  **THIS WHOLE TEST IS DELETED BY `salary:S3-e-2`, and that is the intended
  end of it** (ruling **R-SAL18**, developer 2026-09-06, taken from the
  adversarial design review of `S3-e-1` that raised it).  An axis period
  already CARRIES the fact:
  ``DerivedPeriod.period_id is None`` is exactly *projected past the
  horizon*, because :func:`~app.services.pay_calendar._views.axis_window`
  builds the saved half from materialised rows and the projected half from
  ``projected_paychecks``, which opens at ``horizon + 1`` and stamps
  ``period_id=None``.  Gating on that needs no parameter and no call-site
  wiring, so the pairing this test fences stops being representable rather
  than being checked -- which is what a fence is FOR, and why deleting this
  file is the step succeeding rather than losing work.

  **R-SAL18 rules the accessor and not the raw read, and the difference is
  the whole of it.** `pay_calendar:C2-f2c` deliberately stopped the callee
  knowing how a period spells its primary key -- ``id`` on an ORM row,
  ``period_id`` on a ``DerivedPeriod`` -- so ``S3-e-2`` adds
  ``DerivedPeriod.is_projected`` and ``budget.pay_periods`` grows the same
  member returning ``False``, a saved row being by definition not projected.
  Both period types keep serving and the callee asks a DOMAIN question.
  Reading ``period_id is None`` raw was the smaller diff and was rejected for
  exactly that contract.  Both live callers pass ``DerivedPeriod``s today
  (``resolve_projection_axis`` and ``projection_axis`` return
  ``PeriodWindow``s); one TEST still passes an ORM row, and it is the reason
  the contract is not merely theoretical.
* **An alias or an indirection**: ``f = build_contribution_timeline`` then
  ``f(...)``, and ``getattr(mod, "build_contribution_timeline")(...)`` --
  :func:`_local_names` reads only ``ImportFrom``, so neither name is bound.
  :func:`test_the_blind_spots_are_the_ones_named` pins both.
  **A ``**kwargs`` unpacking is NOT among them, and the first draft of this
  paragraph said it was.** An ``ast.keyword`` whose ``arg`` is ``None`` is
  invisible to the keyword test, so the call reads as *no boundary passed*
  and the census REPORTS it -- a false positive rather than a hole, measured
  2026-09-06 and pinned by
  :func:`test_a_splat_is_reported_rather_than_missed`. Named because the two
  failure directions are not interchangeable: a hole lets a defect ship, and
  this one only refuses a shape that ``app/`` does not use.
* **A horizon computed then bound to a name**: ``h = cal.horizon()`` then
  ``saved_through=h`` reads as a bare ``Name`` and FAILS this census, which is
  a false positive rather than a hole.  Named so a later author knows to
  inline the call rather than to weaken the rule.

The negative case
-----------------

:func:`test_the_scanner_fires_on_a_planted_wrong_boundary` plants the exact
defect the reviews described -- ``opening_bound()`` in place of ``horizon()``
-- and asserts the scanner finds it.  A census returning "no violations" is
indistinguishable from a census that looked in the wrong place.
"""

import ast
from pathlib import Path


#: The producer whose callers are being constrained.
_TIMELINE = "build_contribution_timeline"

#: The keyword carrying the boundary.
_BOUNDARY = "saved_through"

#: The ONE producer of "the last day the saved schedule covers".
_HORIZON = "horizon"

#: The two live call sites, named so a reader sees what the census covers
#: without running it.  A THIRD appearing is not a failure -- it must simply
#: obey the same rule.
_KNOWN_SITES = (
    "app/services/investment_dashboard_service/_chart.py",
    "app/services/retirement_projection.py",
)


def _repo_root() -> Path:
    """Return the repository root, from this file's known depth."""
    return Path(__file__).resolve().parents[2]


def _local_names(tree: ast.AST) -> "set[str]":
    """Return every local name in *tree* bound to the timeline producer.

    Resolved because the ALIAS is what appears at the call site: a census
    matching only the imported spelling would be blind to exactly the module
    that renamed it.

    Args:
        tree: The parsed module.

    Returns:
        The names that call the timeline producer in this module.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _TIMELINE:
                    names.add(alias.asname or alias.name)
    return names


def _boundary_violations(source: str, label: str) -> "list[str]":
    """Return one message per call in *source* that misreads the boundary.

    Args:
        source: Module source.
        label: How to name the module in a failure message.

    Returns:
        A list of human-readable violations; empty when every call obeys.
    """
    tree = ast.parse(source)
    local = _local_names(tree) | {_TIMELINE}
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else None
        )
        if called not in local:
            continue
        passed = {kw.arg: kw.value for kw in node.keywords}
        if _BOUNDARY not in passed:
            problems.append(
                f"{label}:{node.lineno} calls {_TIMELINE} without "
                f"{_BOUNDARY}=; the boundary is the CALENDAR's since plan "
                f"step salary:S3-e-1"
            )
            continue
        value = passed[_BOUNDARY]
        leaf = (
            value.func.attr if isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute) else None
        )
        if leaf != _HORIZON:
            problems.append(
                f"{label}:{node.lineno} passes {_BOUNDARY}= from "
                f"{ast.unparse(value)!r}, not from {_HORIZON}(). The "
                f"transfer average is added PAST the saved schedule and "
                f"nowhere else (ruling salary:R-SAL16)"
            )
    return problems


def _app_violations() -> "list[str]":
    """Return every boundary violation under ``app/``."""
    root = _repo_root()
    problems = []
    for path in sorted((root / "app").rglob("*.py")):
        problems.extend(_boundary_violations(
            path.read_text(encoding="utf-8"),
            str(path.relative_to(root)),
        ))
    return problems


class TestTheBoundaryComesFromTheCalendar:
    """Every ``build_contribution_timeline`` caller reads ``horizon()``."""

    def test_no_caller_invents_its_own_boundary(self):
        """The census over ``app/``."""
        assert _app_violations() == []

    def test_the_census_actually_saw_the_known_sites(self):
        """Non-vacuity: the scanner finds the calls it is meant to grade.

        A census whose matcher silently stopped matching would report zero
        violations exactly as a clean tree does, which is the failure this
        repo has measured many separate ways.
        """
        root = _repo_root()
        found = [
            site for site in _KNOWN_SITES
            if any(
                isinstance(node, ast.Call)
                and (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute) else None
                ) == _TIMELINE
                for node in ast.walk(ast.parse(
                    (root / site).read_text(encoding="utf-8")
                ))
            )
        ]
        assert found == list(_KNOWN_SITES)

    def test_the_scanner_fires_on_a_planted_wrong_boundary(self):
        """The defect two reviews described, planted, and caught."""
        planted = (
            "from app.services.investment_projection import "
            "build_contribution_timeline\n"
            "def f(ctx, feed, periods):\n"
            "    return build_contribution_timeline(\n"
            "        feed=feed, contribution_transactions=[],\n"
            "        periods=periods, as_of=ctx.as_of,\n"
            "        saved_through=ctx.calendar().opening_bound(),\n"
            "    )\n"
        )
        problems = _boundary_violations(planted, "planted.py")
        assert len(problems) == 1
        assert "opening_bound" in problems[0]

    def test_the_scanner_fires_on_an_omitted_boundary(self):
        """The other arm: a caller that passes no boundary at all."""
        planted = (
            "from app.services.investment_projection import "
            "build_contribution_timeline\n"
            "def f(feed, periods, as_of):\n"
            "    return build_contribution_timeline(\n"
            "        feed=feed, contribution_transactions=[],\n"
            "        periods=periods, as_of=as_of,\n"
            "    )\n"
        )
        problems = _boundary_violations(planted, "planted.py")
        assert len(problems) == 1
        assert "without saved_through=" in problems[0]

    def test_the_scanner_passes_a_correctly_wired_caller(self):
        """And it does NOT fire on the shape the live sites use."""
        planted = (
            "from app.services.investment_projection import "
            "build_contribution_timeline\n"
            "def f(ctx, feed, periods):\n"
            "    return build_contribution_timeline(\n"
            "        feed=feed, contribution_transactions=[],\n"
            "        periods=periods, as_of=ctx.as_of,\n"
            "        saved_through=ctx.balance_ctx.calendar().horizon(),\n"
            "    )\n"
        )
        assert _boundary_violations(planted, "planted.py") == []

    def test_the_blind_spots_are_the_ones_named(self):
        """The three shapes the docstring says this cannot see, measured.

        Each is a call the census reports NO violation for while the boundary
        is plainly wrong, so the docstring's limits are executable rather than
        a claim about intent.
        """
        alias = (
            "from app.services.investment_projection import "
            "build_contribution_timeline\n"
            "f = build_contribution_timeline\n"
            "def g(ctx, feed, periods):\n"
            "    return f(feed=feed, contribution_transactions=[],\n"
            "             periods=periods, as_of=ctx.as_of,\n"
            "             saved_through=ctx.calendar().opening_bound())\n"
        )
        getattr_form = (
            "import app.services.investment_projection as ip\n"
            "def g(ctx, feed, periods):\n"
            "    fn = getattr(ip, 'build_contribution_timeline')\n"
            "    return fn(feed=feed, contribution_transactions=[],\n"
            "              periods=periods, as_of=ctx.as_of,\n"
            "              saved_through=ctx.calendar().opening_bound())\n"
        )
        for shape in (alias, getattr_form):
            assert _boundary_violations(shape, "blind.py") == []

    def test_a_splat_is_reported_rather_than_missed(self):
        """A ``**kwargs`` call is a FALSE POSITIVE here, not a blind spot.

        The keyword test cannot see a name inside the unpacking, so the call
        reads as *no boundary passed*.  That direction refuses a correct
        caller rather than admitting a wrong one, which is why the docstring
        separates it from the two real holes.  ``app/`` uses no such call.
        """
        splat = (
            "from app.services.investment_projection import "
            "build_contribution_timeline\n"
            "def g(feed, kw):\n"
            "    return build_contribution_timeline(feed=feed, **kw)\n"
        )
        problems = _boundary_violations(splat, "blind.py")
        assert len(problems) == 1
        assert "without saved_through=" in problems[0]
