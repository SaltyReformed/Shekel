#!/usr/bin/env python3
"""Bake the test template database into a tagged docker image.

The test suite clones a per-worker database from ``shekel_test_template``
for every test.  That template is built by :mod:`scripts.build_test_template`
against a long-lived shared container, which is why the suite needs a slot
lock, a restart flag, a live-backend probe, ``TEST_DB_PREFIX`` and
``TEST_TEMPLATE_DATABASE``: one postmaster, many worktrees.  Give each RUN
its own container and every one of those becomes unnecessary.

This script builds the artifact that makes that affordable: an image whose
PGDATA already contains the template, so starting a run costs a container
boot rather than a migration replay.

THREE THINGS HERE WERE MEASURED, NOT ASSUMED, and each would have produced a
silently wrong image:

1. ``PGDATA`` MUST SIT OUTSIDE THE IMAGE'S DECLARED VOLUME.  The upstream
   image declares ``VOLUME /var/lib/postgresql`` and defaults ``PGDATA`` to
   ``/var/lib/postgresql/18/docker``, inside it -- and ``docker commit`` does
   not capture volume contents.  Committing a container built the default way
   yields an image with an EMPTY cluster and no error anywhere.  Measured: a
   marker database created before the commit was simply gone afterwards.  So
   the build and every run set ``PGDATA`` to :data:`_BAKED_PGDATA`.

2. THE CLUSTER MUST BE SHUT DOWN CLEANLY BEFORE THE COMMIT.  Relying on
   ``docker stop`` alone lost the template once and kept it once -- a race,
   measured both ways.  ``pg_ctl -m fast -w`` is deterministic and also cuts
   cold start from 5,896 ms to 292 ms, because a cleanly-stopped cluster does
   not replay WAL on every boot.

   Do NOT re-derive the reason as "SIGTERM means a smart shutdown".  An
   earlier version of this paragraph said exactly that and it is wrong for
   this image: ``docker image inspect`` reports ``StopSignal=SIGINT``, which
   is PostgreSQL's FAST shutdown.  The likelier cause of the lost template is
   the initdb window described at :func:`_wait_ready` -- a shutdown issued
   while the entrypoint's temporary server is up stops that server rather
   than the real one.  The remedy is unchanged; the explanation was not.

3. THE CACHE KEY IS AN OPTIMISATION, NOT THE CORRECTNESS ARGUMENT.  The
   template is NOT a function of the migrations alone:
   :func:`scripts.build_test_template._populate_template` re-applies in-code
   trigger definitions AFTER ``alembic upgrade``, deliberately, so the latest
   definition wins over the migration-frozen one.  Editing
   ``app/audit_infrastructure.py``, ``app/posting_infrastructure.py`` or
   ``app/opening_infrastructure/`` changes the template without touching
   ``migrations/`` at all.  A key that hashed only the migrations would go
   stale silently, which is the one failure mode that would corrupt results
   rather than merely slow them.  So the key covers every input that runs
   during the build (:data:`KEY_INPUTS`), and -- more importantly -- the
   image is VERIFIED after it is committed, by starting it and re-running the
   builder's own assertions.  A wrong key then costs a rebuild or a loud
   refusal, never a silent wrong-schema run.

Usage::

    python scripts/build_test_db_image.py            # build if absent
    python scripts/build_test_db_image.py --force    # rebuild regardless
    python scripts/build_test_db_image.py --print-tag  # just resolve the tag

Exit codes: 0 on success (image present and verified), 1 on a build or
verification failure, 2 on a usage or environment problem.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The exact postgres build the suite's template is made against.  Kept
# identical to docker-compose.dev.yml's test-db service: a template built on
# one PostgreSQL build and cloned on another is not a supported combination.
_BASE_IMAGE = (
    "postgres:18-alpine@sha256:"
    "96d56f7f57c6aacd1fcb908bc83b345ec5f83231ee486dd66a1baadce274db88"
)

# PGDATA for both the build and every run.  Outside the image's declared
# VOLUME; see point 1 in the module docstring.
_BAKED_PGDATA = "/pgdata-baked"

_IMAGE_REPO = "shekel-test-db"

# Inputs the key covers no matter what the builder imports.  These shape the
# template without being reachable by reading its import block: the migration
# chain itself, alembic's own configuration (``migrations/env.py`` sets
# ``version_table_schema``), the ORM classes ``ref_seeds`` seeds THROUGH, and
# the builder script.
_FIXED_KEY_INPUTS = (
    "migrations/versions",
    "migrations/env.py",
    "alembic.ini",
    "app/models",
    "scripts/build_test_template.py",
)

_TEMPLATE_BUILDER = "scripts/build_test_template.py"

# Modules the builder imports from ``app``, parsed out of the builder itself.
APP_IMPORT = re.compile(
    r"^(?:from|import)\s+(app(?:\.[A-Za-z_][A-Za-z0-9_]*)*)", re.M
)


def key_inputs() -> tuple[str, ...]:
    """Return every path whose content determines the baked template.

    HAND-ENUMERATING THIS WAS WRONG AND WAS MEASURED WRONG.  The first
    version listed four modules by hand -- and missed
    ``app/append_only_infrastructure.py``, which
    ``build_test_template._populate_template`` also re-applies after
    ``alembic upgrade``.  A review changed ``APPEND_ONLY_TABLES`` to attach
    three more triggers to a fourth table and the cache key did not move, so
    the stale image would have been reused and the verifier never run.  That
    is a set defined by recollection, and this repo has paid for that shape
    before.

    So the ``app`` half is DERIVED from the builder's own import block: the
    census comes from the file that does the work, and a future import lands
    in the key without anyone remembering to add it.  The rest is fixed
    because it shapes the template without appearing there -- see
    :data:`_FIXED_KEY_INPUTS`.

    Deriving still cannot see a TRANSITIVE import, which is why
    ``app/models`` is in the fixed half and why the key is only an
    optimisation: :func:`_verify_image` refuses a stale artifact whatever the
    key said, and it now runs on every invocation rather than only after a
    build.

    ``from app import create_app`` resolves to the WHOLE ``app`` package, so
    any ``app/**/*.py`` edit moves the key and forces a rebuild.  That is
    deliberate: it costs 8.5 s (measured 2026-09-05) and it makes transitive
    imports -- which parsing one file's import block cannot see -- stop
    mattering.  Over-hashing wastes seconds; under-hashing ships a stale
    template, which is the failure this whole script exists to prevent.

    Returns:
        Repo-relative paths, sorted and deduplicated.

    Raises:
        BuildError: When the builder cannot be read.
    """
    builder = _REPO_ROOT / _TEMPLATE_BUILDER
    if not builder.exists():
        raise BuildError(f"{_TEMPLATE_BUILDER} is missing; cannot derive the key")
    found: set[str] = set(_FIXED_KEY_INPUTS)
    for dotted in APP_IMPORT.findall(builder.read_text(encoding="utf-8")):
        relative = dotted.replace(".", "/")
        if (_REPO_ROOT / f"{relative}.py").exists():
            found.add(f"{relative}.py")
        elif (_REPO_ROOT / relative).is_dir():
            found.add(relative)
    return tuple(sorted(found))


# Kept as a name because the tests and the error messages refer to it; it is
# now computed rather than declared.
KEY_INPUTS = key_inputs()

_READY_TIMEOUT_SECONDS = 60
_BUILD_USER = "shekel_user"
_BUILD_PASSWORD = "shekel_pass"


class BuildError(RuntimeError):
    """A step of the bake failed, with a message naming which one."""


def _run(
    command: list[str], *, capture: bool = True, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command, raising :class:`BuildError` with its stderr on failure.

    Args:
        command: argv to execute.
        capture: Capture stdout/stderr rather than inheriting them.
        check: Raise when the command exits non-zero.

    Returns:
        The completed process.

    Raises:
        BuildError: When ``check`` and the command exited non-zero.
    """
    result = subprocess.run(
        command, capture_output=capture, text=True, check=False
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise BuildError(
            f"{' '.join(command[:3])}... exited {result.returncode}: {detail}"
        )
    return result


def cache_key() -> str:
    """Return a short hex digest of every input that determines the template.

    Returns:
        Twelve hex characters, stable across machines for identical content.

    Raises:
        BuildError: When a declared input does not exist -- a renamed module
            must update :data:`KEY_INPUTS`, because silently dropping an
            input from the key is exactly the staleness this guards against.
    """
    digest = hashlib.sha256()
    digest.update(_BASE_IMAGE.encode())
    for relative in key_inputs():
        target = _REPO_ROOT / relative
        if not target.exists():
            raise BuildError(
                f"key input {relative!r} does not exist. If it moved, update "
                "KEY_INPUTS -- an input silently dropped from the key is a "
                "silently stale image."
            )
        files = (
            sorted(path for path in target.rglob("*.py") if path.is_file())
            if target.is_dir()
            else [target]
        )
        if target.is_dir() and not files:
            raise BuildError(f"key input {relative!r} contains no .py files")
        for path in files:
            digest.update(str(path.relative_to(_REPO_ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def migration_head() -> str:
    """Return the head revision of the migration chain.

    Computed from the files rather than by importing alembic, so it costs no
    app import and works against a bare checkout.  The head is the revision
    that no other revision names as its ``down_revision``.

    Returns:
        The head revision id.

    Raises:
        BuildError: When the chain has no head or more than one -- both mean
            the schema this image would be verified against is not
            well-defined, and guessing would defeat the check.
    """
    revisions: set[str] = set()
    parents: set[str] = set()
    versions = _REPO_ROOT / "migrations/versions"
    for path in sorted(versions.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        found = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)", text, re.M)
        if found:
            revisions.add(found.group(1))
        for parent in re.findall(
            r"^down_revision(?::[^=]+)?\s*=\s*['\"]([^'\"]+)", text, re.M
        ):
            parents.add(parent)
    heads = revisions - parents
    if len(heads) != 1:
        raise BuildError(
            f"migration chain has {len(heads)} heads ({sorted(heads)}); the "
            "schema to verify against is not well-defined"
        )
    return heads.pop()


def image_tag(key: str | None = None) -> str:
    """Return the image reference for a cache key.

    Args:
        key: Cache key; computed when omitted.

    Returns:
        A ``repo:tag`` reference.
    """
    return f"{_IMAGE_REPO}:{key or cache_key()}"


def image_exists(tag: str) -> bool:
    """Return whether the image is present locally.

    Args:
        tag: Image reference.

    Returns:
        True when ``docker image inspect`` succeeds.
    """
    return _run(["docker", "image", "inspect", tag], check=False).returncode == 0


def _wait_ready(container: str) -> None:
    """Block until PostgreSQL accepts connections, or fail loud.

    Args:
        container: Container name.

    Raises:
        BuildError: When the cluster is not ready within the timeout.
    """
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        # ``-h 127.0.0.1`` forces TCP, and that is the whole point.  The
        # upstream entrypoint runs a TEMPORARY server during initdb which
        # listens on the unix socket ONLY, so a socket-based ``pg_isready``
        # succeeds against a cluster that is still initialising.  A shutdown
        # issued in that window stops the TEMP server; the entrypoint then
        # starts the real one and the container never stops.  Measured: a
        # container still running 60 s after a clean shutdown was requested.
        # The template build hid this because building takes long enough for
        # init to finish first -- a race won by accident, not a guarantee.
        probe = _run(
            [
                "docker", "exec", container, "pg_isready", "-q",
                "-h", "127.0.0.1", "-U", _BUILD_USER,
            ],
            check=False,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.2)
    raise BuildError(
        f"{container} did not accept connections within "
        f"{_READY_TIMEOUT_SECONDS}s"
    )


def _wait_stopped(container: str) -> None:
    """Block until the container is no longer running.

    Args:
        container: Container name.

    Raises:
        BuildError: When it is still running after the timeout, which means
            the cluster never shut down and committing it would capture a
            crash-recovery state.
    """
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        state = _run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            check=False,
        )
        if state.stdout.strip() == "false":
            return
        time.sleep(0.2)
    raise BuildError(
        f"{container} was still running {_READY_TIMEOUT_SECONDS}s after a "
        "shutdown was requested; committing it would capture an unclean "
        "cluster"
    )


def _mapped_port(container: str) -> str:
    """Return the host port docker assigned to the cluster.

    Args:
        container: Container name.

    Returns:
        The host port as a string.

    Raises:
        BuildError: When no port mapping is published.
    """
    mapping = _run(["docker", "port", container, "5432/tcp"]).stdout.strip()
    if not mapping or ":" not in mapping:
        raise BuildError(f"no published port for {container}: {mapping!r}")
    return mapping.rsplit(":", 1)[1]


def _expected_account_types() -> int:
    """Return how many reference account types the template must carry.

    Returns:
        ``len(app.ref_seeds.ACCT_TYPE_SEEDS)``.

    Raises:
        BuildError: When the constant cannot be read.
    """
    return _import_constant("app.ref_seeds", "ACCT_TYPE_SEEDS", length=True)


def _expected_audit_triggers() -> int:
    """Return how many ``audit_*`` triggers the template must carry.

    Returns:
        ``app.audit_infrastructure.EXPECTED_TRIGGER_COUNT``.

    Raises:
        BuildError: When the constant cannot be read.
    """
    return _import_constant("app.audit_infrastructure", "EXPECTED_TRIGGER_COUNT")


def _expected_append_only_triggers() -> int:
    """Return how many append-only triggers the template must carry.

    The module attaches one trigger per statement kind per protected table,
    so the count is derived from the two lists rather than restated -- the
    arithmetic lives next to the thing it counts.

    Returns:
        ``len(APPEND_ONLY_TABLES) * len(APPEND_ONLY_TRIGGERS)``.

    Raises:
        BuildError: When the constants cannot be read.
    """
    tables = _import_constant("app.append_only_infrastructure",
                              "APPEND_ONLY_TABLES", length=True)
    kinds = _import_constant("app.append_only_infrastructure",
                             "APPEND_ONLY_TRIGGERS", length=True)
    return tables * kinds


def _import_constant(module: str, name: str, *, length: bool = False) -> int:
    """Read one integer expectation out of the application package.

    Args:
        module: Dotted module path.
        name: Attribute to read.
        length: Take ``len()`` of the attribute rather than the value.

    Returns:
        The integer expectation.

    Raises:
        BuildError: When the import or the attribute fails, which means the
            producer moved and this verification would otherwise silently
            check the wrong number.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        imported = importlib.import_module(module)
        value = getattr(imported, name)
    except (ImportError, AttributeError) as exc:
        raise BuildError(
            f"cannot read {module}.{name} ({exc}); the verification would "
            "otherwise compare against a number nobody owns"
        ) from exc
    return len(value) if length else int(value)


def _verify_image(tag: str) -> None:
    """Start the committed image and re-run the builder's own assertions.

    This is the correctness argument, and the reason the cache key is only an
    optimisation.  It also catches the class of failure that the clean-
    shutdown requirement exists for: a commit taken over a crash-recovery
    cluster loses its most recent ``CREATE DATABASE``, so the template is
    simply absent and nothing else would notice until a suite run failed
    confusingly.

    Args:
        tag: Image reference to verify.

    Raises:
        BuildError: When the image does not contain a usable template.
    """
    container = f"shekel-verify-{tag.rsplit(':', 1)[1]}-{os.getpid()}"
    _run(["docker", "rm", "-f", container], check=False)
    _run(
        [
            "docker", "run", "-d", "--name", container,
            "-e", f"POSTGRES_USER={_BUILD_USER}",
            "-e", f"POSTGRES_PASSWORD={_BUILD_PASSWORD}",
            "-e", f"PGDATA={_BAKED_PGDATA}",
            "-p", "127.0.0.1::5432", tag,
        ]
    )
    try:
        _wait_ready(container)

        def ask(database: str, sql: str) -> str:
            """Return one scalar from the committed image."""
            return _run(
                [
                    "docker", "exec", container, "psql", "-U", _BUILD_USER,
                    "-d", database, "-tAc", sql,
                ]
            ).stdout.strip()

        present = ask(
            "postgres",
            "SELECT count(*) FROM pg_database "
            "WHERE datname = 'shekel_test_template'",
        )
        if present != "1":
            raise BuildError(
                f"{tag} has no template database (got {present!r}). The "
                "commit did not capture it -- check that PGDATA is outside "
                "the declared VOLUME and that the cluster was shut down "
                "cleanly before committing."
            )

        # EXISTENCE IS NOT ENOUGH, and an earlier draft of this function
        # stopped there while the module docstring claimed it "re-runs the
        # builder's own assertions".  An empty database called
        # shekel_test_template would have passed.  These four are what make
        # the cache key an optimisation rather than the correctness
        # argument: a stale or truncated image is refused here, loudly,
        # whatever the key said.
        head = migration_head()
        # Ask whether the table exists before selecting from it.  An empty
        # database called shekel_test_template otherwise fails with psql's
        # raw `relation "alembic_version" does not exist`, which is true but
        # tells the reader nothing about what to do; measured on a
        # deliberately empty probe image.
        if ask("shekel_test_template", "SELECT to_regclass('alembic_version')") == "":
            raise BuildError(
                f"{tag} has a template database with no alembic_version "
                "table, so it was never migrated. The template is empty -- "
                "check that build_test_template.py actually ran, and rebuild "
                "with --force."
            )
        stamped = ask("shekel_test_template", "SELECT version_num FROM alembic_version")
        if stamped != head:
            raise BuildError(
                f"{tag} is stamped {stamped!r} but the migration chain's head "
                f"is {head!r}. The image is STALE -- it was baked against a "
                "different schema. Rebuild with --force."
            )
        # EXACT counts, from the producer's own constants -- not ``> 0``.
        # An earlier version accepted any positive count, and a probe image
        # carrying 1 of 19 account types and 50 of 52 audit triggers passed.
        # This function is the design's safety net for a wrong cache key, so
        # a threshold weaker than the builder's own assertions defeats it:
        # a template missing an entire trigger family would sail through.
        # The counts are IMPORTED rather than restated, so there is one home
        # for each of them.
        for label, sql, expected in (
            (
                "account types",
                "SELECT count(*) FROM ref.account_types",
                _expected_account_types(),
            ),
            (
                "audit triggers",
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname LIKE 'audit\\_%' AND NOT tgisinternal",
                _expected_audit_triggers(),
            ),
            (
                "append-only triggers",
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname LIKE 'ck\\_append\\_only%' AND NOT tgisinternal",
                _expected_append_only_triggers(),
            ),
        ):
            answer = ask("shekel_test_template", sql)
            if answer != str(expected):
                raise BuildError(
                    f"{tag} failed verification ({label}): got {answer!r}, "
                    f"expected {expected}. The image does not match this "
                    "tree -- rebuild with --force."
                )
        log_rows = ask("shekel_test_template", "SELECT count(*) FROM system.audit_log")
        if log_rows != "0":
            raise BuildError(
                f"{tag} ships {log_rows} audit_log rows; the template must "
                "start from a known zero or every clone inherits them."
            )
        recovered = _run(["docker", "logs", container], check=False)
        if "was not properly shut down" in (recovered.stdout + recovered.stderr):
            raise BuildError(
                f"{tag} boots into crash recovery, so it was committed over "
                "an unclean shutdown. The template may be missing writes; "
                "rebuild with --force."
            )
    finally:
        _run(["docker", "rm", "-f", container], check=False)


def build(tag: str) -> None:
    """Bake the template into ``tag``.

    Args:
        tag: Image reference to create.

    Raises:
        BuildError: When any step fails.
    """
    # The PID suffix is load-bearing in a repo where several checkouts run
    # at once.  The cache key is a CONTENT hash, so two worktrees on the same
    # branch derive the SAME key by construction -- and each of these helpers
    # opens with an unconditional `docker rm -f` on its own name.  Without a
    # per-process suffix the second invocation's first act destroys the
    # first's live bake container mid-migration-replay, and neither takes any
    # lock.
    container = f"shekel-bake-{tag.rsplit(':', 1)[1]}-{os.getpid()}"
    _run(["docker", "rm", "-f", container], check=False)
    print(f"  starting build container from {_BASE_IMAGE.split('@', maxsplit=1)[0]}")
    _run(
        [
            "docker", "run", "-d", "--name", container,
            "-e", f"POSTGRES_USER={_BUILD_USER}",
            "-e", f"POSTGRES_PASSWORD={_BUILD_PASSWORD}",
            "-e", "POSTGRES_DB=postgres",
            "-e", f"PGDATA={_BAKED_PGDATA}",
            "-p", "127.0.0.1::5432", _BASE_IMAGE,
        ]
    )
    try:
        _wait_ready(container)
        port = _mapped_port(container)
        admin = (
            f"postgresql://{_BUILD_USER}:{_BUILD_PASSWORD}"
            f"@127.0.0.1:{port}/postgres"
        )
        print(f"  running build_test_template.py against 127.0.0.1:{port}")
        # ONE producer for the template.  A second migrate-and-seed path
        # written into this file would be rule 14's shape: two spellings of
        # one value, agreeing until they do not.
        builder = subprocess.run(
            [sys.executable, str(_REPO_ROOT / "scripts/build_test_template.py")],
            cwd=str(_REPO_ROOT),
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(Path.home()),
                "TEST_ADMIN_DATABASE_URL": admin,
                "TEST_TEMPLATE_DATABASE": "shekel_test_template",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        if builder.returncode != 0:
            raise BuildError(
                "build_test_template.py failed:\n"
                + (builder.stderr or builder.stdout).strip()
            )
        # Quote the builder's own verification line rather than inferring
        # success from a zero exit.  It prints the counts it checked; a
        # build that somehow produced nothing would still exit 0 if its
        # assertions were skipped, and this is the cheapest place to notice.
        for line in builder.stdout.splitlines():
            if line.startswith(("  Step 3/3", "DONE:")):
                print(f"  builder: {line.strip()}")
        print("  template built; shutting the cluster down cleanly")
        # See point 2 in the module docstring: `docker stop` alone is a race.
        #
        # This exec is NOT checked, and that is not laziness.  postgres is
        # PID 1, so a successful shutdown terminates the container -- which
        # kills the very `docker exec` that asked for it.  Measured: exit
        # 137 (SIGKILL) after "waiting for server to shut down....", i.e.
        # the shutdown WORKED and the messenger died with it.  Checking the
        # status here would fail every successful build.
        #
        # What actually establishes a clean shutdown is two things that do
        # not depend on this exit code: the container reaching a stopped
        # state below, and _verify_image refusing an image that boots into
        # crash recovery.  The instrument is the outcome, not the command.
        _run(
            [
                "docker", "exec", "-u", "postgres", container,
                "pg_ctl", "-D", _BAKED_PGDATA, "-m", "fast", "-w", "stop",
            ],
            check=False,
        )
        _wait_stopped(container)
        print(f"  committing {tag}")
        # Untag any predecessor first so a rebuild does not leave a ~489 MB
        # dangling image behind; `docker commit` to an existing tag orphans
        # the old layers silently, and two such were already on this daemon
        # from probe bakes before anyone noticed.
        _run(["docker", "rmi", "-f", tag], check=False)
        _run(["docker", "commit", container, tag])
    finally:
        _run(["docker", "rm", "-f", container], check=False)

    print("  verifying the committed image")
    try:
        _verify_image(tag)
    except BuildError:
        # DO NOT LEAVE A REFUSED IMAGE TAGGED.  It would be accepted by the
        # next invocation's existence check, and the failure that produced it
        # would never be seen again.
        _run(["docker", "rmi", "-f", tag], check=False)
        raise


def main(argv: list[str] | None = None) -> int:
    """Ensure the template image exists for the current tree.

    Args:
        argv: Command-line arguments; ``sys.argv[1:]`` when omitted.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force", action="store_true", help="rebuild even if the tag exists"
    )
    parser.add_argument(
        "--print-tag",
        action="store_true",
        help="print the tag for this tree and exit without building",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable summary"
    )
    args = parser.parse_args(argv)

    try:
        tag = image_tag()
        if args.print_tag:
            print(tag)
            return 0
        present = image_exists(tag)
        if present and not args.force:
            # VERIFY A CACHED IMAGE TOO.  An earlier version verified only
            # after a build, so a bake whose verification FAILED left the bad
            # image tagged and every later invocation short-circuited on
            # "already present" and returned 0.  Measured: a crash-recovery
            # image was accepted with exit 0 by the very run that would have
            # refused it seconds earlier.  One failed bake poisoned the tag
            # permanently, and reported success.
            print(f"{tag} already present; verifying")
            try:
                _verify_image(tag)
            except BuildError as stale:
                print(f"  cached image rejected: {stale}", file=sys.stderr)
                print("  discarding it and rebuilding")
                _run(["docker", "rmi", "-f", tag], check=False)
                build(tag)
            print(f"DONE: {tag} ready.")
        else:
            print(f"Building {tag}")
            build(tag)
            print(f"DONE: {tag} ready.")
        if args.json:
            print(json.dumps({"tag": tag, "rebuilt": not present or args.force}))
        return 0
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
