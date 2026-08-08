"""``deploy/shekel-deploy.sh`` is DRIVEN, and its safety properties observed.

Plan step **R-F8**, findings **F-8** and **F-14**, ruling **R-R14**.

**Why this file replaced a source-level one.**  The first attempt at gating
R-F8 asserted the ORDER of the script's steps by locating substrings in its
text, on the premise that a behavioural control "needs Docker, two real GHCR
images with different migration sets, and about ninety seconds -- it cannot
run in CI".  Adversarial review demolished both halves.  The premise is false:
every target in the script is an environment override by deliberate design and
every external command resolves through ``PATH``, so a stub ``docker`` drives
the real branch logic in about a second with no daemon at all.  And the
consequence of the premise was worse -- of 45 mutations, **24 stayed green**,
including one that replaced the entire script with a 33-line do-nothing stub
whose ``take_predeploy_dump`` body was a comment reading ``# die die die die``.
Every assertion was a substring-position check, so any file with the right
bytes in the right order satisfied all of them.  Among the mutations that
passed: inverting the migration guard so a migration-bearing release re-pins
(finding F-8, restored verbatim), and making the dump a no-op.

So this file observes OUTCOMES instead: which commands ran and in what order,
what the pin says afterwards, whether a dump exists on disk, and the exit
status.  None of that can be satisfied by a comment.

**The stub.**  ``_write_fake_docker`` emits a ``docker`` shell script onto a
``PATH`` prefix.  It answers the handful of subcommands the deploy script
issues, logs every invocation so ordering is observable, and is scripted per
test through environment variables: which migrations each image "contains",
whether the database container is "running", whether ``pg_dump`` writes a
readable archive, and whether the container ever reports healthy.  Nothing
here touches a real daemon, so these tests are NOT ``@pytest.mark.docker``
and run in CI like any other.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEPLOY_SCRIPT = _REPO_ROOT / "deploy" / "shekel-deploy.sh"

_OLD = "sha256:" + "a" * 64
_NEW = "sha256:" + "b" * 64

#: What a readable ``pg_dump -Fc`` archive starts with.  The stub writes it and
#: the stub's ``pg_restore -l`` requires it, so "the dump is validated before
#: it is renamed into place" becomes an observable fact rather than a claim.
_DUMP_MAGIC = "PGDMP-FAKE"
#: The terminator only a COMPLETE archive carries.
_DUMP_END = "ENDOFARCHIVE"


def _write_fake_docker(bin_dir: pathlib.Path) -> None:
    """Install a scripted ``docker`` stub onto a PATH directory.

    Args:
        bin_dir: Directory placed first on ``PATH`` for the run.
    """
    stub = bin_dir / "docker"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Scripted stand-in for the docker CLI.  Every invocation is logged so
        # the test can assert ORDER; behaviour is driven by FAKE_* env vars.
        printf '%s\\n' "$*" >>"$FAKE_LOG"

        case "$1" in
        info) exit 0 ;;
        pull) exit 0 ;;
        image)
            # `docker image inspect <ref> --format ...` -- resolving :latest.
            printf 'ghcr.io/saltyreformed/shekel@%s\\n' "$FAKE_LATEST_DIGEST"
            exit 0
            ;;
        run)
            # The migration pre-flight: one line per migration file.
            for arg in "$@"; do
                case "$arg" in
                *{_OLD[7:19]}*) printf '%s' "$FAKE_OLD_MIGRATIONS"; exit 0 ;;
                *{_NEW[7:19]}*) printf '%s' "$FAKE_NEW_MIGRATIONS"; exit 0 ;;
                esac
            done
            exit 0
            ;;
        inspect)
            # Two callers: the db running-check and the health poll.
            for arg in "$@"; do
                case "$arg" in
                *State.Running*) printf '%s\\n' "$FAKE_DB_RUNNING"; exit 0 ;;
                *State.Health*)  printf '%s\\n' "$FAKE_HEALTH";     exit 0 ;;
                esac
            done
            exit 0
            ;;
        exec)
            for arg in "$@"; do
                case "$arg" in
                psql)
                    # public.alembic_version.  The stamp ADVANCES once the
                    # deploy has run, which is what a migration at entrypoint
                    # step 3 does and what makes the post-failure re-read
                    # mean something.
                    if grep -q '^compose' "$FAKE_LOG"; then
                        printf '%s\\n' "$FAKE_STAMPED_AFTER"
                    else
                        printf '%s\\n' "$FAKE_STAMPED"
                    fi
                    exit 0
                    ;;
                pg_dump)
                    [ "$FAKE_PGDUMP_RC" != "0" ] && {{
                        printf 'fake pg_dump: %s\\n' "$FAKE_PGDUMP_ERR" >&2
                        exit "$FAKE_PGDUMP_RC"
                    }}
                    # A truncated archive keeps the header (so a TOC-only
                    # read still succeeds) and loses the terminator.
                    if [ "$FAKE_DUMP_TRUNCATED" = "1" ]; then
                        printf '{_DUMP_MAGIC}|payl'
                    else
                        printf '{_DUMP_MAGIC}|payload|{_DUMP_END}'
                    fi
                    exit 0
                    ;;
                pg_restore)
                    # -l reads only the table of contents; -f decodes every
                    # data block.  Modelled separately ON PURPOSE: measured on
                    # a real archive, -l accepts a 99%-truncated dump, so a
                    # script that validated with -l would rename a corrupt
                    # artifact into place and the test must be able to see it.
                    body=$(cat)
                    for a in "$@"; do
                        if [ "$a" = "-l" ]; then
                            case "$body" in
                            {_DUMP_MAGIC}*) exit 0 ;;
                            *) exit 1 ;;
                            esac
                        fi
                    done
                    case "$body" in
                    *{_DUMP_END}) exit 0 ;;
                    *) exit 1 ;;
                    esac
                    ;;
                esac
            done
            exit 0
            ;;
        compose)
            # Record whether a dump already existed when the deploy ran.
            if compgen -G "$FAKE_BACKUP_DIR/*.dump" >/dev/null; then
                printf 'compose-saw-dump\\n' >>"$FAKE_LOG"
            else
                printf 'compose-saw-NO-dump\\n' >>"$FAKE_LOG"
            fi
            exit "$FAKE_COMPOSE_RC"
            ;;
        esac
        exit 0
        """), encoding="utf-8")
    stub.chmod(0o755)


class _Stack:
    """A throwaway deploy target the real script can be pointed at."""

    def __init__(self, tmp_path: pathlib.Path):
        """Build the directory layout and the stub PATH.

        Args:
            tmp_path: pytest's per-test temporary directory.
        """
        self.root = tmp_path
        self.shekel_dir = tmp_path / "stack"
        self.backup_dir = tmp_path / "backups"
        self.bin_dir = tmp_path / "bin"
        for path in (self.shekel_dir, self.backup_dir, self.bin_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.env_file = self.shekel_dir / ".env"
        self.env_file.write_text(
            f"SHEKEL_IMAGE_DIGEST={_OLD}\n", encoding="utf-8"
        )
        self.env_file.chmod(0o600)
        self.log = tmp_path / "docker.log"
        self.log.write_text("", encoding="utf-8")
        _write_fake_docker(self.bin_dir)

    @property
    def pin(self) -> str:
        """The digest currently written into the stack's ``.env``.

        Returns:
            The ``SHEKEL_IMAGE_DIGEST`` value.
        """
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("SHEKEL_IMAGE_DIGEST="):
                return line.split("=", 1)[1]
        raise AssertionError("the stack's .env lost its digest line entirely")

    @property
    def dumps(self) -> list[pathlib.Path]:
        """Completed dump artifacts in the stack's backup directory.

        Returns:
            Every ``*.dump`` file, excluding in-progress ``*.part`` files.
        """
        return sorted(self.backup_dir.glob("*.dump"))

    @property
    def invocations(self) -> list[str]:
        """Every stub ``docker`` invocation, in order.

        Returns:
            One entry per call, plus the ``compose-saw-*`` markers.
        """
        return self.log.read_text(encoding="utf-8").splitlines()

    def run(self, *, target: str = _NEW, old_migrations: str = "0001_a.py\n",
            new_migrations: str = "0001_a.py\n", health: str = "unhealthy",
            db_running: str = "true", pgdump_rc: str = "0",
            pgdump_err: str = "no space left on device",
            dump_truncated: str = "0", stamped: str = "0001",
            stamped_after: str | None = None, dry_run: bool = False,
            compose_rc: str = "0") -> subprocess.CompletedProcess:
        """Run the real deploy script against this stack.

        Args:
            target: Digest to deploy.
            old_migrations: Migration listing the CURRENT image reports.
            new_migrations: Migration listing the TARGET image reports.
            health: What the health poll reports (``healthy``/``unhealthy``).
            db_running: Whether the database container is up.
            pgdump_rc: Exit status for ``pg_dump``.
            pgdump_err: What ``pg_dump`` writes to stderr when it fails.
            dump_truncated: ``1`` to write an archive whose TOC reads but
                whose data blocks are cut short.
            stamped: The revision ``alembic_version`` holds before the deploy.
            stamped_after: The revision it holds once the deploy has run
                (defaults to *stamped*, i.e. nothing migrated).
            dry_run: Pass ``--dry-run``.
            compose_rc: Exit status for ``docker compose``.

        Returns:
            The completed process, with stdout and stderr captured together.
        """
        env = dict(os.environ)
        env.update({
            "PATH": f"{self.bin_dir}:{env['PATH']}",
            "SHEKEL_DIR": str(self.shekel_dir),
            "SHEKEL_BACKUP_DIR": str(self.backup_dir),
            "SHEKEL_CONTAINER_NAME": "probe-app",
            "SHEKEL_DB_CONTAINER": "probe-db",
            "SHEKEL_NTFY_TOKEN_FILE": str(self.root / "no-token"),
            "SHEKEL_HEALTH_TIMEOUT_S": "1",
            "SHEKEL_HEALTH_INTERVAL_S": "1",
            "FAKE_LOG": str(self.log),
            "FAKE_BACKUP_DIR": str(self.backup_dir),
            "FAKE_LATEST_DIGEST": target,
            "FAKE_OLD_MIGRATIONS": old_migrations,
            "FAKE_NEW_MIGRATIONS": new_migrations,
            "FAKE_HEALTH": health,
            "FAKE_DB_RUNNING": db_running,
            "FAKE_PGDUMP_RC": pgdump_rc,
            "FAKE_PGDUMP_ERR": pgdump_err,
            "FAKE_DUMP_TRUNCATED": dump_truncated,
            "FAKE_STAMPED": stamped,
            "FAKE_STAMPED_AFTER": (
                stamped if stamped_after is None else stamped_after
            ),
            "FAKE_COMPOSE_RC": compose_rc,
        })
        argv = ["bash", str(_DEPLOY_SCRIPT), "--no-verify"]
        if dry_run:
            argv.append("--dry-run")
        argv.append(target)
        return subprocess.run(
            argv, env=env, capture_output=True, text=True, timeout=120,
            check=False,
        )


def _flat(text: str) -> str:
    """Collapse whitespace so an assertion can ignore terminal wrapping.

    The script wraps its messages for an 80-column terminal, so a phrase the
    operator reads as one sentence is split across lines in the raw output.

    Args:
        text: Captured stdout/stderr.

    Returns:
        The same text with every run of whitespace reduced to one space.
    """
    return " ".join(text.split())


@pytest.fixture(name="stack")
def _stack(tmp_path) -> _Stack:
    """A throwaway stack with a scripted ``docker`` on PATH.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        The prepared :class:`_Stack`.
    """
    return _Stack(tmp_path)


#: A forward release: the target adds two migrations, and running them moves
#: the stamp to a revision the CURRENT image has never heard of.
_MIGRATION_BEARING = {
    "old_migrations": "0001_a.py\n",
    "new_migrations": "0001_a.py\n0002_b.py\n0003_c.py\n",
    "stamped": "0001",
    "stamped_after": "0003",
}
#: A pure digest revert: identical migration sets, so the stamp cannot move.
_NO_MIGRATIONS = {
    "old_migrations": "0001_a.py\n0002_b.py\n",
    "new_migrations": "0001_a.py\n0002_b.py\n",
    "stamped": "0002",
}
#: A DOWNGRADE: the target is older than the database.  It adds nothing, so
#: the retired "does this release add migrations" test called it safe -- while
#: it is precisely the image that cannot resolve today's schema.
_DOWNGRADE = {
    "old_migrations": "0001_a.py\n0002_b.py\n0003_c.py\n",
    "new_migrations": "0001_a.py\n",
    "stamped": "0003",
}


class TestTheScriptIsInTheRepository:
    """Finding F-14: the path that rolls production used to be untracked.

    It lived only on the host, so it sat outside every linter in the polyglot
    gate and nobody could review it -- which is why F-8 survived unnoticed.
    Kept from the retired source-level file; everything else that file
    asserted is now observed behaviourally above.
    """

    def test_the_canonical_copy_is_present_and_executable(self):
        """`deploy/shekel-deploy.sh` is tracked here and runnable."""
        assert _DEPLOY_SCRIPT.is_file(), (
            "deploy/shekel-deploy.sh is missing -- the production deploy path "
            "is untracked again, which is finding F-14."
        )
        assert _DEPLOY_SCRIPT.stat().st_mode & 0o111, (
            "deploy/shekel-deploy.sh is not executable"
        )


class TestNoDumpNoDeploy:
    """The database is dumped before the deploy, or the deploy does not run."""

    def test_a_dump_exists_before_compose_is_called(self, stack):
        """When compose runs, a completed dump is already on disk.

        Observed, not inferred: the stub records whether a ``*.dump`` existed
        at the moment ``docker compose`` was invoked.
        """
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "compose-saw-dump" in stack.invocations, (
            f"docker compose ran with no dump on disk; 'no dump, no deploy' "
            f"is not what the script does.  Invocations: {stack.invocations}"
        )
        assert len(stack.dumps) == 1

    def test_pg_dump_failing_aborts_before_anything_is_touched(self, stack):
        """A dump failure leaves the pin alone and never starts a container."""
        result = stack.run(pgdump_rc="1", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "no dump, no deploy" in result.stdout + result.stderr
        assert stack.pin == _OLD, "the pin moved despite the dump failing"
        assert not any(i.startswith("compose") for i in stack.invocations), (
            f"a container was started after the dump failed: "
            f"{stack.invocations}"
        )
        assert stack.dumps == [], "a dump artifact survived a failed pg_dump"

    def test_an_unreadable_dump_is_rejected_and_not_renamed(self, stack):
        """A truncated archive fails ``pg_restore -l`` and aborts the deploy.

        The property the ``.part`` rename exists for: a truncated dump wearing
        a valid name is the artifact the refusal path tells the operator to
        restore from.
        """
        result = stack.run(dump_truncated="1", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "truncated" in result.stdout + result.stderr
        assert stack.dumps == [], (
            "an unreadable dump was renamed into place as if it were valid"
        )
        assert list(stack.backup_dir.glob("*.part")) == [], (
            "the rejected .part file was left behind"
        )
        assert stack.pin == _OLD

    def test_the_database_being_down_aborts_the_deploy(self, stack):
        """No reachable database means no dump, so no deploy."""
        result = stack.run(db_running="false", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "no dump, no deploy" in result.stdout + result.stderr
        assert stack.pin == _OLD


class TestTheMigrationBearingReleaseIsNotRePinned:
    """Finding F-8: for such a release, re-pinning is what cannot work."""

    def test_it_refuses_and_leaves_the_pin_at_the_new_digest(self, stack):
        """A failed migration-bearing deploy does not revert the pin."""
        result = stack.run(health="unhealthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "REFUSING to roll back" in output
        assert stack.pin == _NEW, (
            "the script re-pinned the previous digest for a migration-bearing "
            "release -- that is finding F-8: the old image cannot resolve the "
            "revision the database is now stamped at, so it dies too."
        )

    def test_the_refusal_names_the_dump_that_exists(self, stack):
        """The dump path it prints is a file that is actually on disk."""
        result = stack.run(health="unhealthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        assert len(stack.dumps) == 1
        assert str(stack.dumps[0]) in output, (
            "the refusal does not name the dump it took, so the operator has "
            "nothing to restore from"
        )
        assert "pg_restore" in output, "no restore command was given"
        assert "0002_b.py" in output and "0003_c.py" in output, (
            "the refusal does not name the migrations the release added"
        )

    def test_a_compose_failure_also_refuses(self, stack):
        """Fail closed on the other failure path too.

        ``docker compose up -d`` failing usually means the container never
        started, but it also covers one that started and exited having reached
        the migration step.  The two are indistinguishable from the exit
        status, so neither re-pins.
        """
        result = stack.run(compose_rc="1", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "REFUSING to roll back" in result.stdout + result.stderr
        assert stack.pin == _NEW


class TestTheOrdinaryRollbackSurvives:
    """R-R14 forbids removing it: 6 of the last 10 releases were pure reverts."""

    def test_a_release_with_no_new_migrations_reverts_the_pin(self, stack):
        """The automatic rollback still runs, and puts the old digest back."""
        result = stack.run(health="unhealthy", **_NO_MIGRATIONS)
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "REFUSING to roll back" not in output, (
            "the script refused to roll back a release that adds no "
            "migrations; that rollback works and the ruling keeps it"
        )
        assert "rolling back to" in output
        assert stack.pin == _OLD, (
            "the ordinary rollback did not restore the previous digest"
        )

    def test_a_healthy_deploy_keeps_the_new_pin_and_the_dump(self, stack):
        """The success path is unchanged and the restore point is kept."""
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        assert result.returncode == 0
        assert stack.pin == _NEW
        assert len(stack.dumps) == 1
        assert str(stack.dumps[0]) in result.stdout


class TestThePreflightIsHonest:
    """The classification itself, since every branch above turns on it."""

    def test_it_reports_a_migration_bearing_release(self, stack):
        """The added revisions are named, not merely counted."""
        result = stack.run(health="healthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        assert "MIGRATION-BEARING release" in output
        assert "0002_b.py" in output and "0003_c.py" in output
        assert "0001_a.py" not in output.split("adds these revisions")[1][:200]

    def test_dry_run_reports_the_classification_and_changes_nothing(self, stack):
        """``--dry-run`` answers "can this be rolled back?" without deploying."""
        result = stack.run(dry_run=True, health="healthy", **_MIGRATION_BEARING)
        assert result.returncode == 0
        assert "MIGRATION-BEARING release" in result.stdout, (
            "--dry-run does not report whether the release can be rolled back"
        )
        assert stack.pin == _OLD, "--dry-run moved the pin"
        assert stack.dumps == [], "--dry-run took a dump"
        assert not any(
            i.startswith("compose") for i in stack.invocations
        ), "--dry-run started a container"


class TestTheDowngradeCaseTheOldDesignCalledSAFE:
    """A target OLDER than the database adds nothing, and cannot boot.

    The defect adversarial review found in the first implementation of R-F8.
    It classified releases by "does the target ADD migrations", which is
    directional: a rollback deploy adds nothing, was therefore called safe,
    took the ordinary path, and produced the two dead containers F-8 is about.
    The question asked now -- "can the target resolve the revision the
    database is stamped at" -- has no direction and catches this before
    anything is written.
    """

    def test_a_target_older_than_the_database_is_refused_up_front(self, stack):
        """It aborts before the dump, the pin, and any container."""
        result = stack.run(health="healthy", **_DOWNGRADE)
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert (
            "cannot resolve the revision this database is stamped at"
            in _flat(output)
        )
        assert stack.pin == _OLD, "the pin moved for an unbootable target"
        assert stack.dumps == [], "a dump was taken for a refused deploy"
        assert not any(i.startswith("compose") for i in stack.invocations), (
            "a container was started with an image that cannot migrate"
        )

    def test_the_refusal_says_how_a_real_downgrade_is_done(self, stack):
        """It names restore-first rather than leaving the operator stuck."""
        result = stack.run(health="healthy", **_DOWNGRADE)
        output = result.stdout + result.stderr
        assert (
            "restoring a dump taken at or before that revision FIRST"
            in _flat(output)
        )


class TestABrokenProbeIsNeverReadAsSafe:
    """An empty migration listing is a broken probe, not "no migrations".

    ``ls`` in a renamed directory, a changed WORKDIR, a missing ``xargs`` --
    each yields an empty list, and an empty list used to classify the release
    as safe to roll back, silently disabling the whole check.
    """

    def test_an_empty_listing_aborts_rather_than_assuming(self, stack):
        """Zero migrations from an image is refused, loudly."""
        result = stack.run(
            old_migrations="", new_migrations="", stamped="0001",
        )
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "ZERO migrations" in output
        assert stack.pin == _OLD
        assert stack.dumps == []


class TestInitDatabaseStillRaises:
    """R-R14's other prohibition, tested by behaviour rather than by shape.

    The earlier source-level version walked the AST for a ``Try`` containing
    the ``command.upgrade`` call.  Review found five wrappings it missed --
    ``except*`` (which parses to ``ast.TryStar``), ``contextlib.suppress``, a
    decorator, a nested function, and a try at the CALL site -- and two
    harmless shapes it wrongly rejected (``try/finally`` with no handler, and
    ``try/except: raise``).  Asking whether the exception actually escapes has
    none of those gaps.
    """

    def test_an_upgrade_failure_propagates_out_of_migrate(self, monkeypatch):
        """``migrate_existing_database`` does not swallow an Alembic error.

        This is the failure mode F-8 turns on: an unresolvable revision must
        abort the deploy loudly rather than let the app boot against a schema
        it cannot describe.
        """
        # Pylint: ``import-outside-toplevel`` -- the module is loaded by path
        # (scripts/ is not a package), and only this test needs it.
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import load_init_database_module

        module = load_init_database_module()

        def _boom(*_args, **_kwargs):
            """Stand in for a revision Alembic cannot locate."""
            raise RuntimeError("Can't locate revision identified by 'deadbeef'")

        monkeypatch.setattr(module.command, "upgrade", _boom)
        with pytest.raises(RuntimeError, match="Can't locate revision"):
            module.migrate_existing_database()
