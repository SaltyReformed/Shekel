"""Tests for the d4a71f6e30bb "retire the Once pattern's rules" migration.

Plan step **R2e-3** of ``docs/plans/implementation_plan_recurrence_redesign.md``
(rulings **R-R4** and **R-R11**).  The migration NULLs both template tables'
``recurrence_rule_id`` for every ``Once`` rule and then deletes those rules,
while deliberately LEAVING the ``ref.recurrence_patterns`` row itself.

Three groups, and the split is the same one the sibling recurrence migration
tests draw:

* **State at HEAD.**  The migration is already applied when these run (the
  template builder upgraded base->head), so the post-conditions are asserted
  against the live database rather than by re-running the migration in an
  xdist worker.
* **The expand/contract half is asserted in BOTH directions** -- the row is
  present AND no enum member names it.  Either alone passes for the wrong
  reason (see :class:`TestTheSurvivingRefRow`).
* **The downgrade refuses**, read from the AST rather than executed: running a
  downgrade inside an xdist worker would mutate the session-wide database, and
  this one raises by design.

**What this file does NOT do is re-run the upgrade.**  It writes DATA, so
executing it twice against the session database would be a no-op the second
time and prove nothing; the executable evidence is a development-time step,
run against a restore of the prod-clone dev database while building R2e-3
(0 transaction templates and 2 transfer templates detached, 4 rules deleted,
50 rules -> 46, the ``ref`` row intact, both live transfers and all four of
their shadow transactions unchanged).
"""
from __future__ import annotations

import ast
import pathlib

from sqlalchemy import text

from app.enums import RecurrencePatternEnum
from app.extensions import db
from app.ref_seeds import _REF_TABLE_SEEDS
from tests._test_helpers import load_migration_module

_MIGRATION_FILENAME = "d4a71f6e30bb_retire_the_once_recurrence_pattern.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)
_MIGRATION_SOURCE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "migrations" / "versions" / _MIGRATION_FILENAME
).read_text(encoding="utf-8")

#: The pattern this revision retired.  Read from the migration's own constant
#: rather than re-spelled, so the test cannot drift from the statement it
#: grades.
_RETIRED = _MIGRATION.ONCE_PATTERN_NAME


class TestChaining:
    """The revision sits where it claims in the Alembic chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "d4a71f6e30bb"
        assert _MIGRATION.down_revision == "c8f2b6a41d93"

    def test_it_is_reviewed(self):
        """A destructive migration carries the ``Review:`` line the rules require.

        ``.claude/rules/database.md``: a migration that drops or deletes needs
        developer approval recorded in the module docstring.  Asserted because
        this revision DELETEs rows, which no other check in the suite would
        notice the approval was missing from.
        """
        assert "Review:" in _MIGRATION.__doc__


class TestNoOnceRuleSurvives:
    """No ``budget.recurrence_rules`` row can name the retired pattern."""

    def test_zero_rules_reference_the_retired_pattern(self, app):
        """The upgrade's whole point, asserted against the live database.

        **The assertion got STRONGER at plan step R7c-c, and the query
        changed with it.**  It counted the rules whose ``pattern_id`` joined to
        the retired row by NAME -- the honest question while a rule could point
        at one.  That column is dropped (migration ``d9f5c1a48b73``), so what
        is assertable now is that ``ref.recurrence_patterns`` has no inbound
        foreign key at all: no rule NAMES the retired pattern because no rule
        CAN, which is the same claim made unconditionally.

        The ``ref`` row itself still survives, and deliberately -- see
        :class:`TestTheSurvivingRefRow` and ruling R-R11; plan step **R9**
        drops the table.
        """
        with app.app_context():
            referencing = db.session.execute(text("""
                SELECT count(*)
                  FROM pg_constraint c
                 WHERE c.contype = 'f'
                   AND c.confrelid = 'ref.recurrence_patterns'::regclass
            """)).scalar_one()

        assert referencing == 0, (
            f"a foreign key still points at ref.recurrence_patterns, so a "
            f"rule could name the '{_RETIRED}' pattern again.  Plan step "
            f"R7c-c dropped budget.recurrence_rules.pattern_id, which is the "
            f"only one there was -- and dropping it is what turns "
            f"'no surviving rule names it' into 'no rule CAN name it'"
        )

    def test_no_template_of_either_kind_names_a_retired_rule(self, app):
        """Both FK NULL-outs are asserted, not just the rule deletion.

        The FKs are ``ON DELETE SET NULL``, so the DELETE alone would null
        them -- which is exactly why this is worth asserting separately: it
        holds whether the migration's explicit UPDATEs ran or the database
        cascaded them, and it would fail if a future edit dropped both.
        """
        with app.app_context():
            dangling = db.session.execute(text("""
                SELECT count(*) FROM (
                    SELECT t.id FROM budget.transaction_templates t
                     WHERE t.recurrence_rule_id IS NOT NULL
                       AND NOT EXISTS (
                           SELECT 1 FROM budget.recurrence_rules r
                            WHERE r.id = t.recurrence_rule_id)
                    UNION ALL
                    SELECT t.id FROM budget.transfer_templates t
                     WHERE t.recurrence_rule_id IS NOT NULL
                       AND NOT EXISTS (
                           SELECT 1 FROM budget.recurrence_rules r
                            WHERE r.id = t.recurrence_rule_id)
                ) AS both_kinds
            """)).scalar_one()

        assert dangling == 0


class TestTheSurvivingRefRow:
    """The ``ref`` row outlives its enum member, and that is load-bearing.

    Asserted in BOTH directions in one class because either half alone passes
    for the wrong reason: "the row exists" is satisfied by re-adding the enum
    member (which re-introduces the ambiguity ruling R-R4 removed), and "no
    member names it" is satisfied by deleting the row (which is the failure --
    the PREVIOUS image's ``ref_cache.init`` raises without it, and
    ``shekel-deploy`` rolls back to that image).
    """

    def test_the_row_is_still_there(self, app):
        """``ref.recurrence_patterns`` still carries the retired name."""
        with app.app_context():
            rows = db.session.execute(text(
                "SELECT count(*) FROM ref.recurrence_patterns WHERE name = :name"
            ), {"name": _RETIRED}).scalar_one()

        assert rows == 1, (
            f"the '{_RETIRED}' ref row must survive until plan step R9 drops "
            f"the table (ruling R-R11) -- deleting it makes the deploy's "
            f"auto-rollback image unbootable"
        )

    def test_no_enum_member_names_it(self):
        """``RecurrencePatternEnum`` really did lose the member."""
        assert _RETIRED not in {m.value for m in RecurrencePatternEnum}

    def test_the_reseed_would_put_it_back(self):
        """``app/ref_seeds.py`` still lists it, so a fresh init recreates it.

        The database check above cannot see this: every test database is
        migration-built, so the row is present whether or not the reseed list
        names it.  Without the entry, the ``create_all`` + ``seed_reference_data``
        fresh-init path would produce a database the previous image cannot
        boot against.
        """
        seeds = dict(_REF_TABLE_SEEDS)["RecurrencePattern"]

        assert _RETIRED in seeds


class TestDowngradeRefuses:
    """``downgrade`` raises rather than guessing at unrecoverable data.

    Read from the AST rather than executed: this revision's downgrade raises
    by design, and running it in an xdist worker would prove only that
    ``pytest.raises`` works.  Parsed rather than grepped because the docstring
    below it NAMES ``NotImplementedError`` in prose, which a substring search
    would happily accept from a function that raised nothing at all.
    """

    @staticmethod
    def _downgrade_body():
        """Return the ``downgrade`` function's AST node."""
        tree = ast.parse(_MIGRATION_SOURCE)
        return next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )

    def test_downgrade_raises_not_implemented(self):
        """The body contains a real ``raise NotImplementedError(...)``."""
        raises = [
            node for node in ast.walk(self._downgrade_body())
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "NotImplementedError"
        ]

        assert len(raises) == 1

    def test_the_refusal_names_where_the_data_went(self):
        """The message and docstring point at the recoverable source.

        ``.claude/rules/database.md`` requires a refusing downgrade to give
        (a) why it is unsafe and (b) the literal SQL to revert by hand.  Both
        template tables and ``budget.recurrence_rules`` are audited, so
        ``system.audit_log`` holds the deleted rows and the nulled FKs -- and
        a refusal that did not say so would leave an operator with no route
        back at all.
        """
        body = self._downgrade_body()
        message = next(
            node.exc.args[0].value
            for node in ast.walk(body)
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.args[0], ast.Constant)
        )

        assert "system.audit_log" in message
        assert "INSERT INTO budget.recurrence_rules" in ast.get_docstring(body)
        assert "UPDATE budget.transfer_templates" in ast.get_docstring(body)


class TestSelectionIsIdOrderIndependent:
    """The statements select by NAME, never by a literal row id.

    Production and the dev clone both happen to hold ids 1-8 in enum order,
    but a database built through the migration chain does not: ``a3b1c2d4e5f6``
    appends ``quarterly`` and ``semi_annual`` after the initial seed.  A
    literal ``pattern_id = 8`` would delete the wrong rules there.
    """

    def test_the_upgrade_binds_the_pattern_name(self):
        """``upgrade`` passes the name as a bound parameter."""
        tree = ast.parse(_MIGRATION_SOURCE)
        upgrade = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        )
        binds = [
            kw.arg
            for node in ast.walk(upgrade)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "bindparams"
            for kw in node.keywords
        ]

        assert binds and set(binds) == {"once_name"}

    def test_no_statement_hardcodes_a_pattern_id(self):
        """The SQL never compares ``pattern_id`` to a numeric literal."""
        sql = _MIGRATION._ONCE_RULE_IDS  # pylint: disable=protected-access

        assert "SELECT id FROM ref.recurrence_patterns WHERE name = :once_name" in sql
        assert "pattern_id = 8" not in sql
