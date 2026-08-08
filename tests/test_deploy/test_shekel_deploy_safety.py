"""``deploy/shekel-deploy.sh`` keeps the safety ORDER that makes it safe.

Plan step **R-F8**, findings **F-8** and **F-14**, ruling **R-R14**.

**What F-8 was.** Migrations run at entrypoint step 3, BEFORE the health check,
so a failed migration-bearing deploy leaves the database stamped at a revision
the previously-pinned image cannot resolve.  That image then dies at step 3 as
well, so the automatic rollback -- re-pinning the old digest -- turns one dead
container into two.  Reproduced on revision ``d4a71f6e30bb`` as
``CommandError: Can't locate revision``.  F-14 is why it survived unnoticed:
the script was not in the repository, so nobody could review it.

**Why these tests are source-level.**  The behavioural control drives the real
script end to end against a throwaway compose stack with a health check that
cannot pass, and checks that it refuses, names the dump, and still rolls back
normally when the release adds no migrations.  That control needs Docker, two
real GHCR images with DIFFERENT migration sets, and about ninety seconds -- it
cannot run in CI, where none of those exist.  What CI can hold is the ORDER,
and order is the whole property: a dump taken after the pin moves is not a
pre-deploy dump, and a refusal that runs after ``write_env_digest`` has already
re-pinned is not a refusal.  Each assertion below fails if that order is
rearranged.

The two things the ruling says R-F8 must NOT do are pinned here too: the
ordinary rollback stays for a release with no migrations, and
``scripts/init_database.py`` keeps RAISING rather than booting against an
unresolvable revision.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEPLOY_SCRIPT = _REPO_ROOT / "deploy" / "shekel-deploy.sh"
_INIT_DATABASE = _REPO_ROOT / "scripts" / "init_database.py"

#: The section banners that divide the script's main flow.  Asserting on them
#: is not cosmetic: every ordering test below locates a step by its banner, so
#: a renamed or deleted banner has to fail loudly here rather than silently
#: turn an ordering assertion into a no-op.
_BANNERS: tuple[str, ...] = (
    "# ── Migration pre-flight ─",
    "# ── Dry-run exit ─",
    "# ── Pre-deploy dump ─",
    "# ── Deploy ─",
    "# ── Rollback ─",
)


@pytest.fixture(name="script_source", scope="module")
def _script_source() -> str:
    """The deploy script's text.

    Returns:
        ``deploy/shekel-deploy.sh`` decoded as UTF-8.
    """
    return _DEPLOY_SCRIPT.read_text(encoding="utf-8")


def _at(source: str, needle: str) -> int:
    """Return the offset of *needle*, failing the test if it is absent.

    Args:
        source: The script text.
        needle: Literal substring to locate.

    Returns:
        The index of the first occurrence.
    """
    index = source.find(needle)
    assert index != -1, (
        f"deploy/shekel-deploy.sh no longer contains {needle!r}; the ordering "
        f"this file guards cannot be checked against a script that has been "
        f"restructured, so fix the test deliberately rather than deleting it."
    )
    return index


class TestTheScriptIsPresentAndStructured:
    """The script is in the repo (F-14) and its flow banners are intact."""

    def test_the_canonical_copy_is_in_the_repository(self):
        """`deploy/shekel-deploy.sh` exists and is executable.

        F-14: the one path that rolls production used to live only on the
        host, outside every linter in the polyglot gate.
        """
        assert _DEPLOY_SCRIPT.is_file(), (
            "deploy/shekel-deploy.sh is missing -- the production deploy path "
            "is untracked again, which is finding F-14."
        )
        assert _DEPLOY_SCRIPT.stat().st_mode & 0o111, (
            "deploy/shekel-deploy.sh is not executable"
        )

    def test_every_flow_banner_is_present(self, script_source):
        """Each section banner the ordering tests anchor on still exists."""
        for banner in _BANNERS:
            assert banner in script_source, (
                f"the {banner!r} section is gone; the ordering assertions in "
                f"this file anchor on it."
            )


class TestNoDumpNoDeploy:
    """The database is dumped BEFORE the pin is rewritten, or not at all."""

    def test_the_dump_precedes_the_pin_rewrite(self, script_source):
        """`take_predeploy_dump` runs before `write_env_digest "$new_digest"`.

        A dump taken after the pin moved is not a pre-deploy dump: the
        container is already being recreated against the new image, and the
        migrations that make the rollback impossible may already have run.
        """
        dump_at = _at(script_source, "\ntake_predeploy_dump\n")
        repin_at = _at(script_source, 'write_env_digest "$new_digest"')
        assert dump_at < repin_at, (
            "the pre-deploy dump is taken AFTER SHEKEL_IMAGE_DIGEST is "
            "rewritten.  The dump must precede the pin, or 'no dump, no "
            "deploy' is not what the script does."
        )

    def test_a_dump_failure_is_fatal(self, script_source):
        """Every failure inside the dump helper calls `die`, never `log`.

        The property is "no dump, no deploy".  A dump step that warned and
        continued would leave the operator believing there is a restore point
        on exactly the deploy where it matters.
        """
        start = _at(script_source, "take_predeploy_dump() {")
        end = script_source.index("\n}\n", start)
        body = script_source[start:end]
        for failure in ("pg_dump failed", "is not running", "truncated"):
            assert failure in body, (
                f"the dump helper no longer handles the {failure!r} case"
            )
        assert body.count("die ") >= 4, (
            "a failure path in take_predeploy_dump stopped calling die; a "
            "non-fatal dump failure breaks 'no dump, no deploy'"
        )

    def test_the_dump_is_renamed_only_after_it_reads_back(self, script_source):
        """The `.part` file is validated by `pg_restore -l` before the rename.

        A truncated dump wearing a valid name is worse than no dump: it is the
        artifact the refusal path tells the operator to restore from (the
        partial-file discipline of audit finding OPS/SH-04).
        """
        start = _at(script_source, "take_predeploy_dump() {")
        end = script_source.index("\n}\n", start)
        body = script_source[start:end]
        validate_at = body.index("pg_restore -l")
        rename_at = body.index('mv "$part" "$DUMP_PATH"')
        assert validate_at < rename_at, (
            "the dump is renamed into place before pg_restore has read a "
            "table of contents out of it"
        )


class TestTheMigrationBearingReleaseIsNotRePinned:
    """The F-8 refusal, and that it runs INSTEAD of the re-pin."""

    def test_the_preflight_runs_before_the_dry_run_exit(self, script_source):
        """`--dry-run` reports whether the release is migration-bearing.

        "Can this release be rolled back?" is the question the pre-flight
        answers, and it is most useful before deploying, not during.
        """
        preflight_at = _at(script_source, "\npreflight_migrations ")
        dryrun_at = _at(script_source, "# ── Dry-run exit ─")
        assert preflight_at < dryrun_at, (
            "the migration pre-flight runs after the dry-run exit, so "
            "--dry-run cannot report whether the release is migration-bearing"
        )

    def test_the_rollback_refuses_before_it_could_repin(self, script_source):
        """In the rollback section, `refuse_to_repin` precedes any re-pin."""
        rollback_at = _at(script_source, "# ── Rollback ─")
        rollback = script_source[rollback_at:]
        refuse_at = rollback.index("refuse_to_repin ")
        repin_at = rollback.index('write_env_digest "$old_digest"')
        assert refuse_at < repin_at, (
            "the rollback re-pins the old digest before reaching the "
            "migration-bearing refusal, which is exactly finding F-8: for "
            "such a release that re-pin produces a second dead container."
        )

    def test_the_refusal_itself_never_repins(self, script_source):
        """`refuse_to_repin` contains no `write_env_digest` call.

        The name is the contract.  A refusal that quietly re-pinned would be
        the defect wearing the fix's label.
        """
        start = _at(script_source, "refuse_to_repin() {")
        end = script_source.index("\n}\n", start)
        body = script_source[start:end]
        assert "write_env_digest" not in body, (
            "refuse_to_repin rewrites the pin; refusing to re-pin is the "
            "entire point of the function"
        )
        assert "exit 1" in body, "refuse_to_repin must fail the deploy"

    def test_the_refusal_names_the_dump_and_how_to_restore(self, script_source):
        """The refusal is actionable: the dump path and a restore command.

        A refusal that only says "I will not roll back" leaves the operator
        worse off than the broken rollback did.
        """
        start = _at(script_source, "refuse_to_repin() {")
        end = script_source.index("\n}\n", start)
        body = script_source[start:end]
        assert "${DUMP_PATH}" in body, "the refusal does not name the dump"
        assert "pg_restore" in body, (
            "the refusal does not give the restore command"
        )
        assert "$MIGRATION_REVISIONS" in body, (
            "the refusal does not name the migrations the release added"
        )
        assert "docker logs" in body, (
            "the refusal should point at the logs first -- a health failure "
            "that never reached the migration step needs no restore"
        )

    def test_both_failure_paths_branch_on_the_preflight(self, script_source):
        """Compose failure and health failure both consult the pre-flight.

        `docker compose up -d` reporting failure usually means the container
        never started, but it also covers a container that started and exited,
        where the entrypoint DID reach the migration step.  The two are not
        distinguishable from the exit status, so both fail closed.
        """
        deploy_at = _at(script_source, "# ── Deploy ─")
        tail = script_source[deploy_at:]
        assert tail.count("refuse_to_repin ") == 2, (
            f"expected the refusal on BOTH failure paths (compose failure and "
            f"health failure); found {tail.count('refuse_to_repin ')}"
        )


class TestTheTwoThingsTheRulingForbids:
    """R-R14: what R-F8 must NOT do, pinned so a later edit cannot do it."""

    def test_the_no_migration_rollback_survives(self, script_source):
        """A release with no new migrations still rolls back automatically.

        Ruled correct and deliberately kept: 6 of the last 10 releases were
        pure digest reverts, for which the automatic rollback works.
        """
        rollback_at = _at(script_source, "# ── Rollback ─")
        rollback = script_source[rollback_at:]
        assert 'write_env_digest "$old_digest"' in rollback, (
            "the automatic rollback was removed; the ruling keeps it for any "
            "release that adds no migrations"
        )
        assert "compose_up && wait_healthy" in rollback, (
            "the rollback no longer verifies the reverted container is healthy"
        )

    def test_init_database_still_raises_on_an_unresolvable_revision(self):
        """`command.upgrade(..., "head")` is not wrapped in a handler.

        Ruled: the app keeps failing loud rather than booting against a schema
        it cannot describe.  Swallowing this is what would turn F-8 from a
        refused deploy into a silently wrong one, so it is checked
        structurally rather than by reading.
        """
        tree = ast.parse(_INIT_DATABASE.read_text(encoding="utf-8"))
        upgrades = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "upgrade"
        ]
        assert upgrades, (
            "scripts/init_database.py no longer calls command.upgrade(); the "
            "entrypoint's migration step has moved and this test is stale."
        )
        guarded = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            and any(
                call in ast.walk(node)
                for call in upgrades
            )
        ]
        assert guarded == [], (
            "command.upgrade() is inside a try block.  The ruling (R-R14) is "
            "that init_database.py keeps RAISING on an unresolvable revision "
            "rather than booting against a schema it cannot describe."
        )
