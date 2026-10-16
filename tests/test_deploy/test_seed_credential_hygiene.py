"""Deployment-layer assertions for Commit C-34.

Covers audit findings F-022 (SEED_USER_PASSWORD persists in container
env), F-053 (REGISTRATION_ENABLED=true default), F-054 (stale
container retire helper -- RETIRED 2026-06-12: the one-shot script was
deleted after its targets, the pre-rename containers/volumes, were
removed; polyglot audit OPS/SH-25), and F-113 (.dockerignore excludes
dev-only files).  These tests are intentionally
filesystem-and-text-based: they assert on the on-disk shape of the
deployment artefacts that the Phase 6 audit reviewers will inspect,
rather than booting a container to observe runtime behaviour.  The one
exception is F-113's: what the image is built from is docker's answer,
not a file's text, so ``TestTheBuildContextHoldsOnlyWhatTheImageRuns``
asks docker (plan step balance:X-do).

The runtime-behaviour assertions for the Python side of F-022 (env
scrubbing in seed_user.py) live in
tests/test_scripts/test_seed_user.py::TestSeedUserCredentialScrub.
The runtime-behaviour assertions for the route-level F-053 fix
(REGISTRATION_ENABLED=false returns 404) live in
tests/test_routes/test_auth.py::TestRegistrationToggle (existing).
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Files under assertion ──────────────────────────────────────────
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKER_COMPOSE_PROD = REPO_ROOT / "deploy" / "docker-compose.prod.yml"
DOCKER_COMPOSE_DEV = REPO_ROOT / "docker-compose.dev.yml"
ENTRYPOINT_SCRIPT = REPO_ROOT / "entrypoint.sh"
SEED_USER_SCRIPT = REPO_ROOT / "scripts" / "seed_user.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DOCKERFILE = REPO_ROOT / "Dockerfile"
APP_CONFIG = REPO_ROOT / "app" / "config.py"


# ──────────────────────────────────────────────────────────────────
# F-022: SEED_USER_PASSWORD env hygiene
# ──────────────────────────────────────────────────────────────────


class TestSeedCredentialEntrypointScrub:
    """entrypoint.sh must unset SEED_USER_* before exec'ing Gunicorn.

    The unset is the load-bearing fix for F-022: the credentials
    leave the entrypoint shell's environment BEFORE the ``exec "$@"``
    that replaces the shell with Gunicorn, so Gunicorn never inherits
    them in /proc/<pid>/environ.  The ordering matters -- assertions
    check both that the unset exists and that it appears BEFORE the
    final exec.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def entrypoint_text(cls) -> str:
        """Return the full entrypoint.sh source as a string."""
        return ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")

    def test_entrypoint_unsets_seed_password(self, entrypoint_text: str) -> None:
        """entrypoint.sh contains an ``unset SEED_USER_PASSWORD`` line."""
        # We accept either a single multi-arg unset or three separate
        # unsets so a future refactor that splits the line for
        # readability does not break the assertion.  The bash ``unset``
        # builtin accepts multiple arguments.
        assert re.search(
            r"\bunset\b[^\n]*\bSEED_USER_PASSWORD\b",
            entrypoint_text,
        ), (
            "entrypoint.sh does not unset SEED_USER_PASSWORD; the seed "
            "credential will be inherited by Gunicorn in /proc/<pid>/environ "
            "(audit finding F-022)"
        )

    def test_entrypoint_unsets_seed_email(self, entrypoint_text: str) -> None:
        """entrypoint.sh also unsets SEED_USER_EMAIL."""
        assert re.search(
            r"\bunset\b[^\n]*\bSEED_USER_EMAIL\b",
            entrypoint_text,
        ), "entrypoint.sh does not unset SEED_USER_EMAIL"

    def test_entrypoint_unset_runs_before_exec(self, entrypoint_text: str) -> None:
        """The unset MUST appear in source order BEFORE ``exec "$@"``.

        If the unset ran AFTER exec, it would never run -- exec
        replaces the shell process with the named binary and any
        following lines are unreachable.
        """
        # Find the byte offsets of (a) the SEED_USER_PASSWORD unset
        # and (b) the final ``exec "$@"`` that hands off to gunicorn.
        unset_match = re.search(
            r"\bunset\b[^\n]*\bSEED_USER_PASSWORD\b",
            entrypoint_text,
        )
        exec_match = re.search(r'^exec\s+"\$@"', entrypoint_text, re.MULTILINE)
        assert unset_match is not None, "no SEED_USER_PASSWORD unset found"
        assert exec_match is not None, (
            "no ``exec \"$@\"`` line in entrypoint.sh; the script "
            "should hand off to the Dockerfile CMD via exec"
        )
        assert unset_match.start() < exec_match.start(), (
            "SEED_USER_PASSWORD unset appears AFTER ``exec \"$@\"`` in "
            "entrypoint.sh; lines after exec never run, so the unset "
            "is dead code (audit finding F-022 regression)"
        )

    def test_entrypoint_uses_seed_sentinel(self, entrypoint_text: str) -> None:
        """entrypoint.sh checks for the seed-complete sentinel."""
        # The exact filename is documented in the script's comment
        # block and exposed as a SEED_SENTINEL shell variable.
        assert "SEED_SENTINEL=" in entrypoint_text, (
            "entrypoint.sh does not declare the SEED_SENTINEL variable; "
            "the noise-reduction sentinel from Commit C-34 is missing"
        )
        assert "/home/shekel/app/state" in entrypoint_text, (
            "entrypoint.sh does not reference the /home/shekel/app/state "
            "writable mount that the seed sentinel lives under"
        )
        assert re.search(
            r"\bif\s*\[\s*-f\s+\"\$\{SEED_SENTINEL\}\"\s*\]",
            entrypoint_text,
        ), (
            "entrypoint.sh does not gate the seed step on the sentinel "
            "file's existence; subsequent restarts will re-run the "
            "(idempotent but noisy) seed_user.py invocation"
        )

    def test_entrypoint_sentinel_written_after_seed_success(
        self, entrypoint_text: str,
    ) -> None:
        """The sentinel is materialised AFTER the seed script returns.

        The ordering invariant: ``: > "${SEED_SENTINEL}"`` (or
        ``touch``) must appear in the same conditional branch as the
        seed_user.py invocation, AFTER it.  ``set -e`` at the top of
        entrypoint.sh aborts the script on a non-zero return from
        seed_user.py, so the sentinel never gets written on failure.
        """
        # Locate the seed_user.py invocation and the sentinel write
        # in source order.
        seed_match = re.search(
            r"python\s+scripts/seed_user\.py", entrypoint_text,
        )
        sentinel_write_match = re.search(
            r"(touch\s+\"\$\{SEED_SENTINEL\}\"|:\s*>\s*\"\$\{SEED_SENTINEL\}\")",
            entrypoint_text,
        )
        assert seed_match is not None, (
            "entrypoint.sh no longer invokes scripts/seed_user.py"
        )
        assert sentinel_write_match is not None, (
            "entrypoint.sh does not write the seed sentinel file; "
            "subsequent restarts cannot skip the seed step"
        )
        assert seed_match.start() < sentinel_write_match.start(), (
            "Seed sentinel is written BEFORE seed_user.py runs; a "
            "failing seed would leave a stale sentinel that masks "
            "the next retry"
        )


class TestSeedUserScriptScrubsEnv:
    """scripts/seed_user.py must scrub SEED_USER_* in os.environ.

    The Python-side scrub is documented in the module docstring and
    implemented as ``_scrub_seed_env_vars``.  Source-level checks
    here catch a regression where the function is removed or the
    ``finally`` block stops calling it.  The behavioural runtime
    test is in tests/test_scripts/test_seed_user.py.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def script_text(cls) -> str:
        """Return the full scripts/seed_user.py source as a string."""
        return SEED_USER_SCRIPT.read_text(encoding="utf-8")

    def test_script_defines_scrub_helper(self, script_text: str) -> None:
        """``_scrub_seed_env_vars`` is defined in the module."""
        assert "def _scrub_seed_env_vars(" in script_text, (
            "scripts/seed_user.py no longer defines _scrub_seed_env_vars; "
            "the F-022 credential-hygiene helper has been removed"
        )

    def test_scrub_helper_pops_password_and_email(self, script_text: str) -> None:
        """The scrub helper targets SEED_USER_PASSWORD and SEED_USER_EMAIL.

        Asserts on the constant tuple at the top of the file rather
        than on the function body so a refactor that promotes the
        list to a public name still passes.
        """
        # Capture the tuple body and parse out the strings.
        match = re.search(
            r"_SEED_SECRET_ENV_VARS[^=]*=\s*\(([^)]+)\)",
            script_text,
        )
        assert match is not None, (
            "scripts/seed_user.py no longer declares _SEED_SECRET_ENV_VARS"
        )
        body = match.group(1)
        assert '"SEED_USER_PASSWORD"' in body, (
            "_SEED_SECRET_ENV_VARS is missing SEED_USER_PASSWORD"
        )
        assert '"SEED_USER_EMAIL"' in body, (
            "_SEED_SECRET_ENV_VARS is missing SEED_USER_EMAIL"
        )
        # Defensive: SEED_USER_DISPLAY_NAME MUST NOT be in the scrub
        # list (display name is not a secret; see the docstring).
        assert "SEED_USER_DISPLAY_NAME" not in body, (
            "_SEED_SECRET_ENV_VARS includes SEED_USER_DISPLAY_NAME -- the "
            "display name is not a secret and should be retained"
        )

    def test_main_calls_scrub_in_finally(self, script_text: str) -> None:
        """The ``__main__`` block calls scrub from a ``finally`` clause.

        The ``finally`` is what guarantees the scrub runs even when
        seed_user() raises (e.g. DB unreachable, transaction rolled
        back).  A regression that moves the call out of finally would
        let a failing seed leave the credential in os.environ for
        subsequent code paths to read.
        """
        # Match the main block ending with the ``finally:`` containing
        # the scrub call.  We look for the sequence: try, then a
        # finally with _scrub_seed_env_vars().  Use DOTALL so .
        # crosses newlines.
        match = re.search(
            r"if __name__ == \"__main__\":(?:.+?)try:(?:.+?)finally:"
            r"(?:.+?)_scrub_seed_env_vars\(\)",
            script_text,
            re.DOTALL,
        )
        assert match is not None, (
            "scripts/seed_user.py's __main__ block does not call "
            "_scrub_seed_env_vars from a finally clause; a failing seed "
            "would skip the scrub and leak the credential"
        )


# ──────────────────────────────────────────────────────────────────
# F-022: docker-compose.yml + Dockerfile carry the state volume
# ──────────────────────────────────────────────────────────────────


class TestComposeAppStateVolume:
    """The bundled prod compose declares the seed-state volume.

    Without the volume, the seed sentinel would land on the rootfs
    (which Commit C-35 plans to make read-only) or be lost on every
    container restart.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def compose_text(cls) -> str:
        """Return the full docker-compose.yml source as a string."""
        return DOCKER_COMPOSE.read_text(encoding="utf-8")

    def test_app_service_mounts_state_volume(self, compose_text: str) -> None:
        """``shekel-prod-app-state`` is mounted at /home/shekel/app/state."""
        assert (
            "shekel-prod-app-state:/home/shekel/app/state" in compose_text
        ), (
            "docker-compose.yml does not mount the shekel-prod-app-state "
            "volume; the seed sentinel cannot persist across restarts"
        )

    def test_top_level_volumes_declares_state(self, compose_text: str) -> None:
        """The state volume is declared in the top-level volumes block."""
        # Match the top-level ``volumes:`` block (no indentation) and
        # check that ``shekel-prod-app-state:`` (one level of
        # indentation) appears inside it.  Catches a regression where
        # the mount line is added but the volume itself isn't
        # declared, which would fail compose at first ``up``.
        assert re.search(
            r"^volumes:\n(?:\s+\S.*\n)*\s+shekel-prod-app-state:",
            compose_text,
            re.MULTILINE,
        ), (
            "docker-compose.yml mounts shekel-prod-app-state but does not "
            "declare it in the top-level volumes block"
        )

    def test_dockerfile_creates_state_dir(self) -> None:
        """The Dockerfile pre-creates /home/shekel/app/state with shekel ownership.

        Pre-creation matters because Docker's volume-mount semantics
        copy the underlying image path's contents (and ownership) to
        the volume on first creation.  Without the pre-create + chown,
        the volume would be empty and root-owned -- entrypoint.sh's
        ``touch`` would fail under ``set -e`` because it runs as the
        unprivileged shekel user.
        """
        text = DOCKERFILE.read_text(encoding="utf-8")
        assert "/home/shekel/app/state" in text, (
            "Dockerfile does not pre-create /home/shekel/app/state; the "
            "volume mount will be root-owned and entrypoint.sh's "
            "sentinel touch will fail"
        )
        # The state dir is chowned to shekel directly (one line, shared
        # with /var/www/static). Polyglot CI/DF-03 narrowed the prior
        # ``chown -R shekel:shekel /home/shekel/app`` -- which reached the
        # state dir by recursing the whole app tree -- to chown only the
        # two dirs that need it; state still ends up shekel-owned.
        assert "chown shekel:shekel /home/shekel/app/state" in text, (
            "Dockerfile no longer chowns /home/shekel/app/state to shekel; "
            "the state subdirectory will be root-owned and entrypoint.sh's "
            "sentinel touch will fail under set -e"
        )


# ──────────────────────────────────────────────────────────────────
# F-053: REGISTRATION_ENABLED defaults to false in production
# ──────────────────────────────────────────────────────────────────


class TestRegistrationDisabledByDefault:
    """REGISTRATION_ENABLED defaults to false at every production layer.

    Defense-in-depth: docker-compose.yml's interpolation default and
    ProdConfig's getenv default both resolve to false.  Either layer
    in isolation would close the audit finding; together they protect
    against an operator removing one of them by hand.
    """

    def test_docker_compose_default_is_false(self) -> None:
        """docker-compose.yml's REGISTRATION_ENABLED interpolation defaults to false."""
        text = DOCKER_COMPOSE.read_text(encoding="utf-8")
        # Match ``REGISTRATION_ENABLED: ${REGISTRATION_ENABLED:-false}``
        # exactly so a regression to ``:-true`` surfaces immediately.
        assert (
            "REGISTRATION_ENABLED: ${REGISTRATION_ENABLED:-false}" in text
        ), (
            "docker-compose.yml's REGISTRATION_ENABLED interpolation no "
            "longer defaults to ``false``; a deploy without the env var "
            "set in .env would re-open public registration (audit "
            "finding F-053)"
        )
        # Negative assertion: the OLD ``:-true`` default must not be
        # present.  Catches a regression where a maintainer toggles
        # the default back without re-running the audit.
        assert (
            "REGISTRATION_ENABLED: ${REGISTRATION_ENABLED:-true}" not in text
        ), (
            "docker-compose.yml still references the pre-C-34 "
            "``:-true`` default for REGISTRATION_ENABLED"
        )

    def test_env_example_default_is_false(self) -> None:
        """.env.example documents the false-by-default posture."""
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        # The actual setting line.
        assert re.search(
            r"^REGISTRATION_ENABLED=false\b", text, re.MULTILINE,
        ), (
            ".env.example no longer ships REGISTRATION_ENABLED=false; "
            "developers copying the example to .env will get the wrong "
            "production default"
        )

    def test_prodconfig_defaults_to_false(self) -> None:
        """ProdConfig's REGISTRATION_ENABLED defaults to false even with env unset.

        This is the defense-in-depth layer: if a future commit drops
        the ``${REGISTRATION_ENABLED:-false}`` from docker-compose.yml,
        ProdConfig still resolves to False.
        """
        text = APP_CONFIG.read_text(encoding="utf-8")
        # Find the ProdConfig class block and check it carries an
        # explicit override.  We look for the class header + body and
        # then the ``REGISTRATION_ENABLED`` assignment with a
        # ``"false"`` default inside it.
        match = re.search(
            r"class ProdConfig\(BaseConfig\):(.+?)(?=\nclass |\Z)",
            text,
            re.DOTALL,
        )
        assert match is not None, "ProdConfig class not found in app/config.py"
        prod_body = match.group(1)
        assert re.search(
            r"REGISTRATION_ENABLED\s*=\s*os\.getenv\(\s*\n?\s*"
            r"\"REGISTRATION_ENABLED\",\s*\"false\"",
            prod_body,
        ), (
            "ProdConfig does not override REGISTRATION_ENABLED with a "
            "``false`` default; only the docker-compose interpolation "
            "default protects production -- defense-in-depth missing"
        )


# ──────────────────────────────────────────────────────────────────


class TestDockerignoreCoverage:
    """The ``.dockerignore`` header names what the image keeps.

    What the build context HOLDS is graded by docker itself, in
    ``TestTheBuildContextHoldsOnlyWhatTheImageRuns`` below: the per-line
    checks this class used to make were re-expressed there on what they
    protect (plan step balance:X-do, finding BAL-625; the developer's
    rule-5 OK, 2026-10-16), because ``.dockerignore`` is now an allowlist
    and a line in it says less than what docker builds from it.  The check
    left here is NOW VACUOUS: the three names it looks for are the
    allowlist's own ``!`` lines, so it passes with every comment deleted.
    Whether it is deleted or narrowed to the comment lines is a rule-5
    question for the developer, reported by that step and not decided here.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def dockerignore_text(cls) -> str:
        """Return the full .dockerignore source as a string."""
        return DOCKERIGNORE.read_text(encoding="utf-8")

    def test_dockerignore_documents_intent(
        self, dockerignore_text: str,
    ) -> None:
        """The file's header comments document what the image keeps and excludes.

        Discoverability matters here: a developer adding a new
        top-level directory needs to know whether to add a corresponding
        ignore line.  The header block lists the runtime essentials
        explicitly so the question answers itself.
        """
        # Look for the inventory of runtime-essential paths in the
        # leading comment block.  This is a soft assertion -- the
        # exact wording can drift -- so we only check that the key
        # markers are present.
        for marker in ("entrypoint.sh", "init_db.sql", "seed_user.py"):
            assert marker in dockerignore_text, (
                f".dockerignore header comment block no longer mentions "
                f"``{marker}``; future maintainers will not know the "
                "runtime-essential file inventory without spelunking "
                "entrypoint.sh"
            )


# ──────────────────────────────────────────────────────────────────
# F-113: the build context holds only what the image runs, by
# docker's own answer
# ──────────────────────────────────────────────────────────────────

#: A Dockerfile that copies the build context, whole, onto an empty image --
#: what the real Dockerfile's ``COPY --chown=shekel:shekel . .`` copies -- so
#: BuildKit's local exporter writes back exactly what the context held.
_CONTEXT_EXPORT_DOCKERFILE = "FROM scratch\nCOPY . /\n"

#: Seconds one context export may take.  ``FROM scratch`` pulls nothing and
#: the allowlisted context is about 1,100 files.
_CONTEXT_EXPORT_TIMEOUT_S = 40

#: The documents whose ``docker exec ... python scripts/<name>.py`` lines tell
#: an operator to run a script INSIDE the container: the top-level docs and
#: the two READMEs.  The dated records under docs/ subdirectories (audits/,
#: historical/, plans/) govern nothing and are not read.
_OPERATOR_DOCS = (
    *sorted((REPO_ROOT / "docs").glob("*.md")),
    REPO_ROOT / "README.md",
    REPO_ROOT / "deploy" / "README.md",
)

#: A ``scripts/`` file named in entrypoint.sh's text.
_SCRIPT_PATH = re.compile(r"\bscripts/[A-Za-z0-9_]+\.(?:py|sql)\b")

#: A script an operator document runs with ``docker exec``.
_DOCKER_EXEC_SCRIPT = re.compile(
    r"docker (?:compose )?exec\b[^\n`|]*?\bpython3?\s+(scripts/[A-Za-z0-9_]+\.py)",
)

#: The migration runner, which opens Alembic's config and history by path.
_MIGRATION_RUNNER = REPO_ROOT / "app" / "migration_runner.py"

#: A path the migration runner joins to the repository root.
_REPO_ROOT_JOIN = re.compile(r'os\.path\.join\(\s*_REPO_ROOT,\s*"([^"]+)"\s*\)')

#: Seconds ``git ls-files`` may take.
_GIT_TIMEOUT_S = 30


def _export_build_context(context: Path, dest: Path) -> set[str]:
    """Return every file docker puts in the build context of ``context``.

    Docker itself reads the context's ``.dockerignore`` (BuildKit's local
    exporter writes the copied context to ``dest``), so the answer is docker's
    matcher's, not a reimplementation of it: a second spelling of docker's
    pattern rules would agree with itself exactly where it disagreed with
    docker -- root anchoring, ``**`` and ``!`` precedence -- which is where
    the old deny-list drifted unseen.

    Returns:
        The context's files, as paths relative to its root.
    """
    result = subprocess.run(
        [
            "docker", "build",
            "--output", f"type=local,dest={dest}",
            "--file", "-",
            str(context),
        ],
        input=_CONTEXT_EXPORT_DOCKERFILE,
        capture_output=True,
        text=True,
        timeout=_CONTEXT_EXPORT_TIMEOUT_S,
        check=False,
    )
    assert result.returncode == 0, (
        f"docker could not build the context of {context}:\n{result.stderr}"
    )
    return {
        path.relative_to(dest).as_posix()
        for path in dest.rglob("*")
        if not path.is_dir()
    }


def _dockerfile_exec_form(instruction: str) -> list[str]:
    """Return the argument list of the Dockerfile's last exec-form ``instruction``."""
    lines = [
        line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{instruction} [")
    ]
    assert lines, f"Dockerfile has no exec-form {instruction} line"
    return json.loads(lines[-1][len(instruction):])


def _scripts_imports(path: Path) -> set[str]:
    """Return the ``scripts/<name>.py`` files the module at ``path`` imports.

    Every ``scripts.<name>`` import anywhere in the module counts, a
    function-local one included: over-counting only asks the context for a
    file it should hold anyway.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == "scripts":
            found.update(f"scripts/{alias.name}.py" for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("scripts."):
            found.add(node.module.replace(".", "/") + ".py")
        elif isinstance(node, ast.Import):
            found.update(
                alias.name.replace(".", "/") + ".py"
                for alias in node.names if alias.name.startswith("scripts.")
            )
    return found


def _tracked(*paths: str) -> set[str]:
    """Return the tracked files at, or under, each repository-relative path."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--", *paths],
        capture_output=True, text=True, timeout=_GIT_TIMEOUT_S, check=True,
    )
    return {name for name in result.stdout.split("\0") if name}


def _packages_imported_by(path: Path) -> set[str]:
    """Return this repository's top-level packages the module at ``path`` imports."""
    names: list[str] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return {
        name.split(".")[0] for name in names
        if (REPO_ROOT / name.split(".")[0] / "__init__.py").is_file()
    }


def _files_the_image_runs() -> set[str]:
    """Return the files the image runs, each read off the file that runs it.

    Seven consumers, and the static closure of their ``scripts.*`` imports:
    the Dockerfile's ENTRYPOINT (relative to its WORKDIR), its CMD (gunicorn's
    ``--config`` file and the module it serves) and its other COPY sources;
    every tracked file of each package the served module imports; the paths
    the migration runner joins to the repository root (a directory's tracked
    files, all of them); the ``scripts/`` files entrypoint.sh names; and the
    scripts the operator documents run with ``docker exec``.  Each consumer
    must name at least one file, so a pattern that stopped matching cannot
    pass by finding nothing.
    """
    from_dockerfile, served_file = _read_off_the_dockerfile()
    served_packages = _packages_imported_by(REPO_ROOT / served_file)
    runner_paths = set(_REPO_ROOT_JOIN.findall(_MIGRATION_RUNNER.read_text(encoding="utf-8")))
    from_entrypoint = set(_SCRIPT_PATH.findall(ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")))
    from_docs = {
        match
        for doc in _OPERATOR_DOCS
        for match in _DOCKER_EXEC_SCRIPT.findall(doc.read_text(encoding="utf-8"))
    }
    for source, named in (
        (f"{served_file}'s imports", served_packages),
        ("the migration runner", runner_paths),
        ("entrypoint.sh", from_entrypoint),
        ("the operator docs", from_docs),
    ):
        assert named, f"nothing found in {source}: the pattern reads nothing"

    required = (
        from_dockerfile | from_entrypoint | from_docs
        | _tracked(*sorted(served_packages | runner_paths))
    )
    pending = [name for name in required if name.endswith(".py")]
    while pending:
        for imported in _scripts_imports(REPO_ROOT / pending.pop()):
            if imported not in required:
                required.add(imported)
                pending.append(imported)
    return required


def _read_off_the_dockerfile() -> tuple[set[str], str]:
    """Return the files the Dockerfile itself names, and the module gunicorn serves.

    The ENTRYPOINT (relative to the WORKDIR), the CMD's ``--config`` file and
    served module, and every COPY source but ``.`` and another stage's.
    """
    workdir = [
        line.split(maxsplit=1)[1]
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("WORKDIR ")
    ][-1]
    entrypoint = _dockerfile_exec_form("ENTRYPOINT")[0]
    assert entrypoint.startswith(f"{workdir}/"), (entrypoint, workdir)
    cmd = _dockerfile_exec_form("CMD")
    served_file = cmd[-1].split(":", 1)[0].replace(".", "/") + ".py"
    copied = {
        source
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("COPY ") and "--from=" not in line
        for source in line.split()[1:-1]
        if not source.startswith("--") and source != "."
    }
    assert copied, "nothing found in the Dockerfile's COPY lines: the pattern reads nothing"
    return {
        entrypoint[len(workdir) + 1:], cmd[cmd.index("--config") + 1], served_file, *copied,
    }, served_file


def _is_credential_file(name: str) -> bool:
    """True for a ``.env`` or ``.env.*`` file other than the template ``.env.example``."""
    base = name.rsplit("/", 1)[-1]
    return (base == ".env" or base.startswith(".env.")) and base != ".env.example"


def _is_tooling(name: str) -> bool:
    """True for a file the image must never carry: host or test tooling, or bytecode.

    A shell script other than the ENTRYPOINT runs on the host (backup,
    restore, deploy, the hooks); ``tests/``, ``tools/`` and any
    ``conftest.py`` are the suite's and the developer's; ``__pycache__`` and
    ``*.pyc`` are what a host checkout's own runs leave.
    """
    parts = name.split("/")
    return (
        (name.endswith(".sh") and name != "entrypoint.sh")
        or parts[0] in {"tests", "tools"}
        or parts[-1] == "conftest.py"
        or "__pycache__" in parts
        or name.endswith((".pyc", ".pyo"))
    )


@pytest.mark.docker
class TestTheBuildContextHoldsOnlyWhatTheImageRuns:
    """What docker copies into the image: everything it runs, and no credential or tooling.

    The re-expression of the deny-list's per-line checks on their subject
    (plan step balance:X-do, finding BAL-625; the developer's rule-5 OK,
    2026-10-16): no seed credential and no host-only script is in what the
    image is built from, and a NEW file stays out until it is named, which
    no line of a deny-list could grade.

    The census's DIRECTION: the required files are read off seven consumers
    (``_files_the_image_runs`` names them) and the static closure of their
    ``scripts.*`` imports.  What it CANNOT see, each of which would fail no
    test here if its ``!`` line went: a file read by path anywhere but the
    migration runner's joins to the repository root; ``seed_companion.py``,
    which no operator document runs; and ``LICENSE``, kept for the licence
    with no consumer at all (``.env.example`` is graded by the credential
    test instead).  The never-shipped side is graded only for what
    ``_is_tooling`` names (shell scripts, tests/, tools/, ``conftest.py``,
    bytecode) and, through the planted context, for files nobody named: a
    host-only Python script or a directory re-included BY NAME
    (``!scripts/check_tax_law.py``, ``!docs/``) passes every test here.
    Only a run of the image grades the boot path.

    A selected docker test that cannot reach docker FAILS here rather than
    skipping: ``conftest.py`` in this package already decides when these
    run (CI, an isolated daemon, or the explicit override), and a skip under
    that decision would leave CI's gate thinner with nothing in its report.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def repo_context(cls, tmp_path_factory: pytest.TempPathFactory) -> set[str]:
        """Return the files docker puts in this repository's build context."""
        return _export_build_context(REPO_ROOT, tmp_path_factory.mktemp("context"))

    def test_every_file_the_image_runs_is_in_the_context(
        self, repo_context: set[str],
    ) -> None:
        """Each file a consumer runs, and each module those import, is copied in."""
        missing = sorted(_files_the_image_runs() - repo_context)
        assert not missing, (
            f"the image's build context lacks {missing}: .dockerignore's "
            "allowlist must name each file the container runs"
        )

    def test_no_credential_file_is_in_the_context(
        self, repo_context: set[str],
    ) -> None:
        """No ``.env`` or ``.env.*`` is copied in; the template ``.env.example`` is."""
        credentials = sorted(name for name in repo_context if _is_credential_file(name))
        assert not credentials, (
            f"{credentials} would be baked into the image: a .env holds live "
            "credentials (audit finding F-113)"
        )
        assert ".env.example" in repo_context, (
            "the image no longer carries .env.example, the template "
            "docs/runbook_secrets.md rebuilds a .env from"
        )

    def test_no_host_or_test_tooling_is_in_the_context(
        self, repo_context: set[str],
    ) -> None:
        """No host script, test or tool file, ``conftest.py`` or bytecode is copied in."""
        tooling = sorted(name for name in repo_context if _is_tooling(name))
        assert not tooling, (
            f"{tooling} would ship in the production image; it runs on the "
            "host or in the suite, never in the container (audit finding F-113)"
        )

    def test_a_new_file_stays_out_until_it_is_named(self, tmp_path: Path) -> None:
        """Files nobody named -- a .env, a new script, a new directory -- stay out.

        A context of planted files under this repository's ``.dockerignore``:
        only the ones its allowlist names come back, so a file added
        tomorrow ships only once someone adds its line.
        """
        context = tmp_path / "context"
        planted = {
            # Named by the allowlist: these come back.
            "entrypoint.sh": True,
            "scripts/seed_user.py": True,
            "app/new_module.py": True,
            ".env.example": True,
            # Named by nothing: these stay out.
            ".env": False,
            ".env.production": False,
            "scripts/new_host_tool.sh": False,
            "scripts/new_tool.py": False,
            "conftest.py": False,
            "tests/conftest.py": False,
            "tools/new_tool.py": False,
            "new_directory/settings.toml": False,
            "NEW_TOP_LEVEL_FILE": False,
            "app/__pycache__/new_module.cpython-314.pyc": False,
            "app/routes/__pycache__/x.cpython-314.pyc": False,
            "app/stray.pyc": False,
            "app/.env": False,
            "migrations/.env.local": False,
            "app/conftest.py": False,
        }
        for name in planted:
            path = context / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"planted {name}\n", encoding="utf-8")
        shutil.copyfile(DOCKERIGNORE, context / ".dockerignore")

        copied = _export_build_context(context, tmp_path / "exported")

        expected = {name for name, ships in planted.items() if ships}
        assert copied == expected, (
            f"shipped but named by nothing: {sorted(copied - expected)}; "
            f"named but left out: {sorted(expected - copied)}"
        )


# ──────────────────────────────────────────────────────────────────
# Integration: docker-compose.yml + entrypoint.sh agree on paths
# ──────────────────────────────────────────────────────────────────


class TestComposeEntrypointAgreement:
    """The state-volume mount path matches the entrypoint sentinel path.

    A drift here would silently break the sentinel (entrypoint.sh
    would write to the rootfs path while the volume sits empty at a
    different mount point).
    """

    def test_state_mount_path_matches_sentinel_path(self) -> None:
        """docker-compose.yml mounts the volume at the path entrypoint.sh writes to."""
        compose_text = DOCKER_COMPOSE.read_text(encoding="utf-8")
        entrypoint_text = ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")

        # Extract the mount target from docker-compose.yml.
        mount_match = re.search(
            r"shekel-prod-app-state:([^\s:]+)", compose_text,
        )
        assert mount_match is not None, (
            "docker-compose.yml does not mount shekel-prod-app-state"
        )
        mount_target = mount_match.group(1)

        # Extract the sentinel directory from entrypoint.sh.
        sentinel_dir_match = re.search(
            r'SEED_STATE_DIR="([^"]+)"', entrypoint_text,
        )
        assert sentinel_dir_match is not None, (
            "entrypoint.sh does not declare SEED_STATE_DIR"
        )
        sentinel_dir = sentinel_dir_match.group(1)

        assert mount_target == sentinel_dir, textwrap.dedent(f"""
            Mount path drift between docker-compose.yml and entrypoint.sh:
              docker-compose.yml mounts at:  {mount_target}
              entrypoint.sh writes to:        {sentinel_dir}
            The sentinel will land on the read-only rootfs (or be lost
            on every restart) instead of the persistent volume.
        """).strip()
