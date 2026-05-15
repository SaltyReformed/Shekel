"""Tests for remediation Commit C-36: Dockerfile + image supply-chain refresh.

Covers audit findings:

    F-025  OpenSSL packages in container image have available updates
    F-060  Container image pins to ``:latest`` tag
    F-062  Two HIGH OS CVEs with no fix available in container image
    F-120  pip CVE in container (build-time only)
    F-155  No Cosign / image-signature verification

The artifacts under audit are the multi-stage Dockerfile, the
production compose override at ``deploy/docker-compose.prod.yml``,
``scripts/deploy.sh`` (Cosign sign + verify wrappers),
``.github/workflows/docker-publish.yml`` (CI signing pipeline),
and ``.gitignore`` (private-key exclusion).

Tests are filesystem/text-based: the fast unit suite must not depend
on a live Docker daemon, GHCR network, or installed Cosign binary.
A separate subprocess test (``TestComposeOverrideRequiresDigest``)
runs ``docker compose config`` to exercise the merged interpolation
behaviour and skips when Docker is not available.

The runtime-behaviour tests (``cosign verify`` against a real signed
image, trivy scan delta) are the manual verification step in the
remediation plan and are not automated here -- automating them
would require a valid signing key on the test host and a full
re-pull of the multi-megabyte image on every suite run.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Files under assertion ──────────────────────────────────────────
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
PROD_COMPOSE_OVERRIDE = REPO_ROOT / "deploy" / "docker-compose.prod.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
GITIGNORE = REPO_ROOT / ".gitignore"
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.sh"
DEPLOY_README = REPO_ROOT / "deploy" / "README.md"
DOCKER_PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"

# Pattern matching ``FROM <image>@sha256:<64 hex chars>``.  We do not
# pin to a specific digest here because the digest is expected to
# rotate over time; the assertion is that the digest pin EXISTS on
# every FROM line, not that it points at one specific image.
DIGEST_PIN_PATTERN = re.compile(
    r"^FROM\s+[^\s]+@sha256:[0-9a-f]{64}\b",
    re.MULTILINE,
)

# Pattern matching the ``SHEKEL_IMAGE_DIGEST`` interpolation in the
# prod override.  Required-form (``:?``) is what makes the missing-
# digest case a hard parse failure instead of a silent fallback to
# ``:latest``.
REQUIRED_DIGEST_INTERPOLATION_PATTERN = re.compile(
    r"image:\s+ghcr\.io/saltyreformed/shekel@\$\{SHEKEL_IMAGE_DIGEST:\?[^}]+\}",
)

DOCKER_SUBPROCESS_TIMEOUT_S = 60


def _docker_available() -> bool:
    """Return True when the ``docker`` CLI is on PATH and the daemon
    answers ``docker info``.  Mirrors the helper in the sibling
    test_deploy_configs.py / test_container_hardening.py modules.
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


# ──────────────────────────────────────────────────────────────────
# F-025 + F-062 + F-120: Dockerfile pins by digest, runs OpenSSL
#   upgrade in both stages, upgrades pip past CVE-2026-1703.
# ──────────────────────────────────────────────────────────────────


class TestDockerfileDigestPin:
    """Dockerfile FROM lines pin the base image by sha256 digest.

    The audit complaint (F-060 for the GHCR image, F-025 for the
    Python base) is that floating tags allow silent image swaps and
    defeat reproducible builds.  Pinning by sha256 digest closes
    that surface.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the full Dockerfile source as a string."""
        return DOCKERFILE.read_text(encoding="utf-8")

    def test_both_stages_pin_by_digest(self, text: str) -> None:
        """Both FROM lines must include ``@sha256:<digest>``.

        The Dockerfile is multi-stage: a builder stage that installs
        psycopg2 build deps and a runtime stage that copies only the
        venv.  A digest pin on only one stage would let the other
        stage drift to whatever ``:latest`` resolves to at build
        time -- defeating reproducibility.
        """
        matches = DIGEST_PIN_PATTERN.findall(text)
        # The Dockerfile has exactly two FROM lines (builder + runtime).
        assert len(matches) == 2, (
            f"expected exactly 2 FROM lines pinned by ``@sha256:`` digest "
            f"in Dockerfile, got {len(matches)}.  Both stages must pin "
            f"to the same digest for reproducible builds (audit "
            f"findings F-025, F-060)."
        )

    def test_both_stages_share_one_digest(self, text: str) -> None:
        """The builder and runtime FROM lines reference the same digest.

        A drift between the two would mean stage 2's runtime libraries
        (libssl3t64 in particular) might come from a different OpenSSL
        revision than the one stage 1 linked psycopg2 against.  The
        symmetry is the load-bearing invariant.
        """
        from_lines = [
            line for line in text.splitlines()
            if line.startswith("FROM ")
        ]
        digests = []
        for line in from_lines:
            match = re.search(r"@(sha256:[0-9a-f]{64})", line)
            if match is not None:
                digests.append(match.group(1))
        assert len(digests) >= 2, (
            f"Dockerfile has fewer than 2 digest-pinned FROM lines: "
            f"{from_lines}"
        )
        unique = set(digests)
        assert len(unique) == 1, (
            f"Dockerfile FROM lines pin to different digests: {unique}.  "
            f"Stage 1 (builder) and stage 2 (runtime) must use the same "
            f"base image digest so OpenSSL revisions match across stages."
        )

    def test_no_floating_tag_only_from(self, text: str) -> None:
        """No FROM line uses a tag without a digest pin.

        Catches a regression where a maintainer reverts to
        ``FROM python:3.14-slim`` (no digest).  The committed file
        either pins by digest or the test fails.
        """
        from_lines = [
            line.strip() for line in text.splitlines()
            if line.strip().startswith("FROM ")
        ]
        for line in from_lines:
            assert "@sha256:" in line, (
                f"FROM line lacks ``@sha256:`` digest pin: {line!r}.  "
                f"Replace with ``FROM <image>@sha256:<digest>`` "
                f"(audit findings F-025, F-060)."
            )


class TestDockerfileOpenSSLUpgrade:
    """Both stages run apt-get upgrade for the OpenSSL packages.

    Defense-in-depth on top of the digest pin: the digest gives
    reproducibility, the apt-get upgrade gives currency.  A new CVE
    that lands in Debian's trixie repos between digest refreshes is
    picked up on the next image build.  Audit finding F-025.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the full Dockerfile source as a string."""
        return DOCKERFILE.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "package", ["openssl", "libssl3t64", "openssl-provider-legacy"]
    )
    def test_dockerfile_mentions_openssl_package(
        self, text: str, package: str,
    ) -> None:
        """Each OpenSSL package the audit names must appear in the
        Dockerfile's apt-get upgrade list.  The audit explicitly
        names all three (the vulnerable package set in F-025).
        """
        assert package in text, (
            f"Dockerfile does not mention {package!r} -- the audit "
            f"specifically requires upgrading this package "
            f"(F-025)."
        )

    def test_apt_get_upgrade_appears_at_least_twice(self, text: str) -> None:
        """``apt-get upgrade`` runs in both the builder and runtime stages.

        We count occurrences rather than parse the Dockerfile because
        each stage is a separate sequence of RUN directives and the
        upgrade must run in EACH stage (the runtime stage gets its own
        libssl3t64 from the postgresql-client install).
        """
        # Find all distinct apt-get upgrade invocations.  We allow
        # the upgrade to be on one line or split across continuation
        # lines, so the search is for the literal substring within
        # any run command.
        upgrade_count = len(re.findall(r"apt-get upgrade -y", text))
        assert upgrade_count >= 2, (
            f"``apt-get upgrade -y`` appears {upgrade_count} time(s) in "
            f"Dockerfile; both the builder and runtime stages must run "
            f"the upgrade so libssl3t64 in the runtime stage is also "
            f"patched (audit finding F-025)."
        )


class TestDockerfilePipUpgrade:
    """The Dockerfile upgrades pip past the CVE-2026-1703 fix.

    Audit finding F-120: pip 25.3 has a path-traversal CVE (low
    impact because pip only runs at build time).  Fixed in pip 26.0+.
    The base image already ships pip 26.0.1 but the explicit upgrade
    in the Dockerfile defends against a future base-image regression.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the full Dockerfile source as a string."""
        return DOCKERFILE.read_text(encoding="utf-8")

    def test_pip_upgrade_to_at_least_26(self, text: str) -> None:
        """A ``pip install --upgrade`` line constrains pip to >= 26.0.

        We accept ``pip>=26.0``, ``pip==26.0``, ``pip==26.0.1``, or
        any equivalent constraint -- the binding constraint is
        "fixes CVE-2026-1703".  pip 26.0 is the minimum.
        """
        # Match common forms.  ``pip>=26`` is acceptable too (>=26
        # implies >=26.0).
        pattern = re.compile(
            r"pip\s+install\s+(?:--no-cache-dir\s+)?--upgrade\s+"
            r"['\"]?pip(?:[<>=!~]+|==)\s*26",
        )
        assert pattern.search(text) is not None, (
            "Dockerfile does not run ``pip install --upgrade pip>=26`` "
            "(audit finding F-120 -- CVE-2026-1703 path-traversal fix "
            "lands in pip 26.0).  Add the upgrade BEFORE "
            "``pip install -r requirements.txt`` so requirements are "
            "installed with the patched pip."
        )

    def test_pip_upgrade_runs_before_requirements_install(
        self, text: str,
    ) -> None:
        """The pip upgrade appears in source order before the
        ``pip install -r requirements.txt`` line so the requirements
        are resolved with the patched pip, not the pre-CVE pip.
        """
        upgrade_match = re.search(
            r"pip\s+install\s+(?:--no-cache-dir\s+)?--upgrade\s+['\"]?pip",
            text,
        )
        requirements_match = re.search(
            r"pip\s+install\s+(?:--no-cache-dir\s+)?-r\s+requirements\.txt",
            text,
        )
        assert upgrade_match is not None, (
            "no ``pip install --upgrade pip`` line in Dockerfile"
        )
        assert requirements_match is not None, (
            "no ``pip install -r requirements.txt`` line in Dockerfile"
        )
        assert upgrade_match.start() < requirements_match.start(), (
            "Dockerfile installs requirements.txt BEFORE upgrading pip; "
            "the requirements would be resolved with the pre-CVE pip "
            "(audit finding F-120)."
        )


class TestDockerfilePreservesC34Invariants:
    """The C-36 refresh must not regress the C-34 / C-35 hardening.

    C-34 added the ``/home/shekel/app/state`` pre-create + chown for
    the seed sentinel volume.  A careless rewrite of the Dockerfile
    could drop those lines.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the full Dockerfile source as a string."""
        return DOCKERFILE.read_text(encoding="utf-8")

    def test_state_dir_is_precreated(self, text: str) -> None:
        """``/home/shekel/app/state`` is created and shekel-owned.

        See tests/test_deploy/test_seed_credential_hygiene.py
        (TestComposeAppStateVolume) for the full rationale.  Repeated
        here so a C-36-only test run also catches the regression.
        """
        assert "/home/shekel/app/state" in text, (
            "Dockerfile no longer pre-creates /home/shekel/app/state; "
            "regresses C-34 / F-022."
        )
        assert "chown -R shekel:shekel /home/shekel/app" in text, (
            "Dockerfile no longer chowns /home/shekel/app to shekel; "
            "regresses C-34 / F-022."
        )

    def test_runs_as_unprivileged_shekel_user(self, text: str) -> None:
        """``USER shekel`` appears so Gunicorn runs as the
        unprivileged user.  Required for the C-35 cap_drop ALL +
        no-new-privileges hardening to remain effective.
        """
        assert "USER shekel" in text, (
            "Dockerfile no longer drops to the shekel user; the C-35 "
            "cap_drop ALL hardening assumes the process is already "
            "non-root.  Restore ``USER shekel`` before EXPOSE."
        )

    def test_healthcheck_present(self, text: str) -> None:
        """The HEALTHCHECK directive survives the C-36 refresh.

        Composer and entrypoint depend on the health endpoint to
        gate the rolling restart in scripts/deploy.sh.
        """
        assert "HEALTHCHECK" in text, (
            "Dockerfile lost its HEALTHCHECK directive; deploy.sh's "
            "wait_for_health() relies on /health responding."
        )


# ──────────────────────────────────────────────────────────────────
# F-060: Image digest pinning lives in the prod override.
# ──────────────────────────────────────────────────────────────────


class TestProdComposeOverridePinsByDigest:
    """deploy/docker-compose.prod.yml interpolates SHEKEL_IMAGE_DIGEST
    into the app service's image reference and FAILS LOUDLY when the
    variable is missing.  Audit findings F-060, F-155 / Commit C-36.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the full prod override source as a string."""
        return PROD_COMPOSE_OVERRIDE.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def parsed(self) -> dict:
        """Return the parsed prod override document.

        Class-scoped so the YAML is parsed once and reused across
        every test in this class.
        """
        with PROD_COMPOSE_OVERRIDE.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_app_service_overrides_image(self, parsed: dict) -> None:
        """The app service in the override declares an ``image:`` key.

        Without an override on this key, the merged compose would
        inherit the base file's ``ghcr.io/saltyreformed/shekel:latest``
        and the digest pin never takes effect.
        """
        app = parsed["services"]["app"]
        assert "image" in app, (
            "deploy/docker-compose.prod.yml does not override "
            "services.app.image; the base ``:latest`` reference would "
            "be used in shared-mode production (audit finding F-060)."
        )

    def test_image_uses_required_digest_interpolation(self, text: str) -> None:
        """The image override uses ``${SHEKEL_IMAGE_DIGEST:?...}``.

        The ``:?`` syntax is the load-bearing fix: it makes the
        missing-variable case a hard parse failure with a clear
        remediation message, instead of silently falling back to a
        floating tag.
        """
        assert REQUIRED_DIGEST_INTERPOLATION_PATTERN.search(text) is not None, (
            "deploy/docker-compose.prod.yml does not interpolate "
            "SHEKEL_IMAGE_DIGEST with the ``:?`` required-form syntax.  "
            "Use the literal pattern: "
            "``ghcr.io/saltyreformed/shekel@${SHEKEL_IMAGE_DIGEST:?...}`` "
            "so a missing digest fails ``docker compose up`` rather "
            "than silently pulling whatever ``:latest`` resolves to "
            "(audit finding F-060)."
        )

    def test_image_namespace_matches_repository(self, text: str) -> None:
        """The image reference points at the right GHCR repo.

        A typo in the namespace would either pull the wrong image
        (best case: 404 at pull time) or pull a publicly-accessible
        image with a matching name (worst case).
        """
        assert "ghcr.io/saltyreformed/shekel@" in text, (
            "deploy/docker-compose.prod.yml does not pin "
            "ghcr.io/saltyreformed/shekel; check the namespace."
        )


class TestBaseComposeDocumentsOverride:
    """The base docker-compose.yml carries a comment block warning
    operators that production must override the ``:latest`` tag with
    a digest pin.  Discoverability matters: an end user reading the
    base file must not assume ``:latest`` is the prod-safe default.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the full base compose source as a string."""
        return DOCKER_COMPOSE.read_text(encoding="utf-8")

    def test_image_line_carries_c36_pointer(self, text: str) -> None:
        """The block above the ``image:`` line references C-36 and F-060.

        The comment is the documentation surface for the override
        requirement; a regression that drops it would silently
        weaken the discoverability of the production hardening.
        """
        # Find the app service block start, then read forward to the
        # image: line.  The pointer comment must appear within ~50
        # lines of the image: line (it sits directly above it in the
        # current layout).
        app_match = re.search(r"^  app:\s*$", text, re.MULTILINE)
        assert app_match is not None, "no ``app:`` service in base compose"
        # Find the image line after the app service marker.
        image_match = re.search(
            r"^    image:\s*ghcr\.io/saltyreformed/shekel:latest\b",
            text[app_match.start():],
            re.MULTILINE,
        )
        assert image_match is not None, (
            "base docker-compose.yml's app service lost the "
            "``image: ghcr.io/saltyreformed/shekel:latest`` line.  If "
            "the image was moved entirely to the prod override, this "
            "test must be updated to assert on the override instead."
        )
        # Slice the block from app: to the image: line and look for
        # the audit pointer.
        block = text[app_match.start():app_match.start() + image_match.end()]
        assert "F-060" in block, (
            "base docker-compose.yml's app service block no longer "
            "mentions F-060 above the image: line; the C-36 "
            "documentation marker is missing.  Operators reading the "
            "base file would not know that ``:latest`` is intentionally "
            "non-production-safe."
        )
        assert "C-36" in block, (
            "base docker-compose.yml's app service block no longer "
            "mentions C-36 above the image: line; the documentation "
            "trail to the digest-pinning override is broken."
        )


class TestEnvExampleDocumentsDigestVariable:
    """``.env.example`` declares SHEKEL_IMAGE_DIGEST so the operator
    knows to set it.  An undocumented required variable would only
    surface as a compose parse error at first ``up`` -- bad UX for
    a production deployment.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the full .env.example source as a string."""
        return ENV_EXAMPLE.read_text(encoding="utf-8")

    def test_shekel_image_digest_present(self, text: str) -> None:
        """``SHEKEL_IMAGE_DIGEST=`` appears as an active assignment.

        The empty value documents the operator's responsibility
        without baking a placeholder digest that could be mistaken
        for a real pin.
        """
        assert re.search(
            r"^SHEKEL_IMAGE_DIGEST=", text, re.MULTILINE,
        ), (
            ".env.example does not declare SHEKEL_IMAGE_DIGEST; "
            "operators reading the example would not know about the "
            "required variable until ``docker compose up`` failed at "
            "parse time (audit finding F-060)."
        )

    def test_documents_remediation_workflow(self, text: str) -> None:
        """The variable's leading comment block points at the
        rotation procedure in deploy/README.md.  Discoverability:
        the reader can find the next step without context-switching.
        """
        # The pointer must appear somewhere in the comment block above
        # the assignment.  We slice the file from the comment marker
        # for the digest section to the assignment line and assert on
        # the substring.
        match = re.search(
            r"((?:^#.*\n)+)SHEKEL_IMAGE_DIGEST=",
            text,
            re.MULTILINE,
        )
        assert match is not None, (
            ".env.example's SHEKEL_IMAGE_DIGEST has no leading comment "
            "block; the operator has no inline guidance."
        )
        block = match.group(1)
        for marker in ("F-060", "C-36", "deploy/README.md"):
            assert marker in block, (
                f".env.example's SHEKEL_IMAGE_DIGEST comment block does "
                f"not mention {marker!r}; the documentation trail is "
                f"incomplete."
            )


class TestComposeOverrideRequiresDigest:
    """Subprocess test: ``docker compose config`` against the merged
    base + prod override must FAIL when SHEKEL_IMAGE_DIGEST is unset
    and SUCCEED with the digest pinned.

    This is the highest-fidelity check: it exercises compose's actual
    interpolation engine, so a regression in the ``:?`` syntax or in
    the env var name surfaces here even if the file-content tests
    above pass.
    """

    @pytest.fixture
    def base_env(self) -> dict[str, str]:
        """Minimal env that satisfies the OTHER required interpolations
        in the base compose (POSTGRES_PASSWORD, SECRET_KEY,
        APP_ROLE_PASSWORD).  Each test then layers SHEKEL_IMAGE_DIGEST
        on top (or omits it deliberately).
        """
        return {
            "PATH": (
                "/usr/local/bin:/usr/local/sbin:/usr/bin:"
                "/usr/sbin:/bin:/sbin"
            ),
            "POSTGRES_PASSWORD": "test-postgres-password",
            "SECRET_KEY": "a" * 32,
            "APP_ROLE_PASSWORD": "test-app-role-password",
            # Phase B3 Redis ACL hardening interpolation.
            "SHEKEL_REDIS_PASSWORD": "test-redis-password",
        }

    def _run_compose_config(self, env: dict[str, str]) -> subprocess.CompletedProcess:
        """Helper: run ``docker compose config`` against the merged
        base + prod override with a synthetic env."""
        return subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "-f",
                str(DOCKER_COMPOSE),
                "-f",
                str(PROD_COMPOSE_OVERRIDE),
                "config",
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
            cwd=str(REPO_ROOT),
            env=env,
        )

    def test_missing_digest_fails_compose_parse(
        self, base_env: dict[str, str],
    ) -> None:
        """Without SHEKEL_IMAGE_DIGEST, ``docker compose config`` exits
        non-zero with a remediation message that mentions the variable
        name.  This is the load-bearing security property: the
        operator CANNOT accidentally deploy without pinning a digest.
        """
        if not _docker_available():
            pytest.skip("docker not available")
        result = self._run_compose_config(base_env)
        assert result.returncode != 0, (
            f"docker compose config succeeded WITHOUT "
            f"SHEKEL_IMAGE_DIGEST set; the ``:?`` required-form "
            f"interpolation is broken.\n"
            f"  stdout: {result.stdout}\n  stderr: {result.stderr}"
        )
        # The error message must reference the env var so the operator
        # knows what to set.  The actual message comes from the ``:?``
        # remediation text in deploy/docker-compose.prod.yml.
        combined = result.stdout + result.stderr
        assert "SHEKEL_IMAGE_DIGEST" in combined, (
            f"compose error does not mention SHEKEL_IMAGE_DIGEST; the "
            f"operator would not know what to set.\n"
            f"  stdout: {result.stdout}\n  stderr: {result.stderr}"
        )

    def test_pinned_digest_succeeds(
        self, base_env: dict[str, str],
    ) -> None:
        """With SHEKEL_IMAGE_DIGEST set to a valid sha256 reference,
        the merged compose parses and the resulting image: line
        carries the digest.
        """
        if not _docker_available():
            pytest.skip("docker not available")
        # 64 hex chars after sha256: -- the canonical OCI digest shape.
        synthetic_digest = "sha256:" + "0" * 64
        env = {**base_env, "SHEKEL_IMAGE_DIGEST": synthetic_digest}
        result = self._run_compose_config(env)
        assert result.returncode == 0, (
            f"docker compose config failed WITH SHEKEL_IMAGE_DIGEST "
            f"set to {synthetic_digest!r}; the override pinning is "
            f"broken.\n"
            f"  stdout: {result.stdout}\n  stderr: {result.stderr}"
        )
        merged = result.stdout
        expected_image = (
            f"ghcr.io/saltyreformed/shekel@{synthetic_digest}"
        )
        assert expected_image in merged, (
            f"merged compose does not carry the pinned image "
            f"reference {expected_image!r}; check the ``image:`` "
            f"override in deploy/docker-compose.prod.yml.\n"
            f"  stdout: {result.stdout}"
        )


# ──────────────────────────────────────────────────────────────────
# F-155: Cosign sign + verify wired into deploy.sh and CI.
# ──────────────────────────────────────────────────────────────────


class TestDeployScriptCosignWrappers:
    """scripts/deploy.sh defines sign + verify functions and calls
    both before swapping the running container.  Audit finding
    F-155 / Commit C-36.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the full deploy.sh source as a string."""
        return DEPLOY_SCRIPT.read_text(encoding="utf-8")

    def test_script_parses_cleanly(self) -> None:
        """``bash -n`` accepts the script (no syntax errors).

        All other tests in this class assume the script is parseable.
        """
        result = subprocess.run(
            ["bash", "-n", str(DEPLOY_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, (
            f"bash -n failed for {DEPLOY_SCRIPT}:\n"
            f"  stdout: {result.stdout}\n  stderr: {result.stderr}"
        )

    def test_defines_sign_image_function(self, text: str) -> None:
        """``sign_image()`` exists and references cosign.

        Asserts on the function definition specifically (not just any
        ``cosign sign`` substring) so a regression that removes the
        function but leaves a comment matching the keyword still fails.
        """
        assert re.search(r"^sign_image\(\)\s*\{", text, re.MULTILINE), (
            "scripts/deploy.sh does not define ``sign_image()``; the "
            "C-36 cosign signing wrapper is missing."
        )
        # The function body must call ``cosign sign``.
        sign_block = re.search(
            r"sign_image\(\)\s*\{(.+?)^\}", text,
            re.MULTILINE | re.DOTALL,
        )
        assert sign_block is not None
        assert "cosign sign" in sign_block.group(1), (
            "sign_image() body does not call ``cosign sign``; the "
            "wrapper is a no-op."
        )

    def test_defines_verify_image_signature_function(self, text: str) -> None:
        """``verify_image_signature()`` exists and calls cosign verify."""
        assert re.search(
            r"^verify_image_signature\(\)\s*\{", text, re.MULTILINE,
        ), (
            "scripts/deploy.sh does not define "
            "``verify_image_signature()``; the C-36 cosign verify "
            "wrapper is missing."
        )
        verify_block = re.search(
            r"verify_image_signature\(\)\s*\{(.+?)^\}", text,
            re.MULTILINE | re.DOTALL,
        )
        assert verify_block is not None
        assert "cosign verify" in verify_block.group(1), (
            "verify_image_signature() body does not call ``cosign verify``"
        )

    def test_main_calls_sign_before_verify_before_restart(self, text: str) -> None:
        """main() calls ``sign_image`` then ``verify_image_signature``
        then ``restart_app`` in source order.

        Ordering invariant: signing must precede verification (verify
        exercises the just-emitted signature, catching key mismatches
        before the swap), and verification must precede restart (so an
        unverifiable image never reaches the running container).
        """
        # Find the main() function block.
        main_match = re.search(
            r"^main\(\)\s*\{(.+?)^\}", text,
            re.MULTILINE | re.DOTALL,
        )
        assert main_match is not None, "no main() in deploy.sh"
        main_body = main_match.group(1)

        sign_call = re.search(r"^\s*sign_image\b", main_body, re.MULTILINE)
        verify_call = re.search(
            r"^\s*verify_image_signature\b", main_body, re.MULTILINE,
        )
        restart_call = re.search(
            r"^\s*restart_app\b", main_body, re.MULTILINE,
        )

        assert sign_call is not None, (
            "main() does not call sign_image()"
        )
        assert verify_call is not None, (
            "main() does not call verify_image_signature()"
        )
        assert restart_call is not None, (
            "main() does not call restart_app() (regression in the "
            "core deploy flow, not just the cosign wrapping)"
        )

        # Source-order assertions.
        assert sign_call.start() < verify_call.start(), (
            "main() calls verify_image_signature() BEFORE sign_image(); "
            "verify must exercise the just-produced signature so a "
            "key/verifier mismatch surfaces before the swap."
        )
        assert verify_call.start() < restart_call.start(), (
            "main() calls restart_app() BEFORE "
            "verify_image_signature(); an unverifiable image would "
            "be deployed to the running container."
        )

    def test_skip_cosign_flag_is_documented(self, text: str) -> None:
        """``--skip-cosign`` appears in the usage block and the
        argument parser.  Operators need an emergency-bypass path.
        """
        assert "--skip-cosign" in text, (
            "scripts/deploy.sh does not declare a --skip-cosign flag; "
            "operators have no way to bypass cosign in an emergency."
        )
        # The argument parser case statement must handle the flag.
        assert re.search(
            r'--skip-cosign\)\s*\n\s*SKIP_COSIGN=true',
            text,
        ), (
            "scripts/deploy.sh's argument parser does not set "
            "SKIP_COSIGN=true on --skip-cosign; the flag is documented "
            "but inert."
        )

    def test_cosign_required_env_var_handled(self, text: str) -> None:
        """The script reads COSIGN_REQUIRED so steady-state ops can
        promote warnings to errors via .env without changing the
        invocation.
        """
        assert "COSIGN_REQUIRED" in text, (
            "scripts/deploy.sh does not reference COSIGN_REQUIRED; "
            "operators cannot enforce cosign in steady-state without "
            "passing a flag on every invocation."
        )

    def test_cosign_public_key_default_points_to_deploy(self, text: str) -> None:
        """COSIGN_PUBLIC_KEY defaults to ``deploy/cosign.pub`` so a
        committed verifier key auto-applies without operator config.
        """
        assert "deploy/cosign.pub" in text, (
            "scripts/deploy.sh does not default COSIGN_PUBLIC_KEY to "
            "deploy/cosign.pub; operators would have to set the path "
            "explicitly even when using the committed key."
        )


class TestDockerPublishWorkflowSignsImage:
    """``.github/workflows/docker-publish.yml`` installs cosign and
    signs the just-pushed image with keyless OIDC.  Audit finding
    F-155 / Commit C-36.
    """

    @pytest.fixture(scope="class")
    def parsed(self) -> dict:
        """Return the parsed workflow YAML."""
        with DOCKER_PUBLISH_WORKFLOW.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the raw workflow text for substring assertions."""
        return DOCKER_PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_grants_id_token_write(self, parsed: dict) -> None:
        """Keyless OIDC requires ``id-token: write`` permissions.

        Without this, ``cosign sign`` fails with a 403 from the OIDC
        token endpoint.  We assert on the structured permissions
        block (not just the substring) so a regression that
        accidentally removes the permission surfaces here.
        """
        job = parsed["jobs"]["build-and-push"]
        permissions = job.get("permissions") or {}
        assert permissions.get("id-token") == "write", (
            "build-and-push job does not grant id-token: write; "
            "cosign keyless OIDC will fail with a 403 (audit finding "
            "F-155).  Add it to the job's permissions block."
        )

    def test_workflow_installs_cosign(self, parsed: dict) -> None:
        """A step uses sigstore/cosign-installer with a pinned version.

        Pinning the cosign version prevents an upstream API change
        from silently breaking signing in steady state.
        """
        steps = parsed["jobs"]["build-and-push"]["steps"]
        installer_step = next(
            (s for s in steps if "sigstore/cosign-installer" in (s.get("uses") or "")),
            None,
        )
        assert installer_step is not None, (
            ".github/workflows/docker-publish.yml does not install "
            "cosign via sigstore/cosign-installer; the sign step "
            "below would fail with ``cosign: not found``."
        )
        # Assert the installer pins a cosign release.
        with_args = installer_step.get("with") or {}
        cosign_release = with_args.get("cosign-release")
        assert cosign_release, (
            "sigstore/cosign-installer step does not pin "
            "cosign-release; an upstream cosign API change could "
            "break signing without a code change."
        )

    def test_workflow_signs_with_keyless_oidc(self, text: str) -> None:
        """A step invokes ``cosign sign --yes`` against the build digest.

        We check on the raw text because the actual sign command is
        in a multi-line shell ``run:`` block that's awkward to assert
        on via the parsed YAML.  ``--yes`` is required to suppress
        the interactive confirmation that would otherwise block CI.
        """
        assert "cosign sign --yes" in text, (
            ".github/workflows/docker-publish.yml does not run "
            "``cosign sign --yes``; without --yes the sign step "
            "blocks waiting for an interactive confirmation that "
            "never arrives in CI (audit finding F-155)."
        )
        # The sign target must reference the digest, not a tag.
        # Signing by tag would let a tag swap detach the signature
        # from the image bytes it attests to.
        assert "${DIGEST}" in text or "$DIGEST" in text, (
            ".github/workflows/docker-publish.yml does not pass the "
            "build digest to cosign sign; signing by tag alone is "
            "the F-155 anti-pattern (a tag swap would silently "
            "detach the signature)."
        )

    def test_workflow_outputs_digest_for_pinning(self, text: str) -> None:
        """The workflow emits the build digest in a discoverable spot.

        The digest is what the operator pastes into
        SHEKEL_IMAGE_DIGEST; without an emit step, every deploy
        requires the operator to look up the digest manually.
        """
        # Match either the workflow summary write or a literal echo.
        assert "GITHUB_STEP_SUMMARY" in text, (
            ".github/workflows/docker-publish.yml does not write the "
            "build digest to GITHUB_STEP_SUMMARY; operators have no "
            "discoverable surface for the SHEKEL_IMAGE_DIGEST value."
        )
        assert "SHEKEL_IMAGE_DIGEST" in text, (
            ".github/workflows/docker-publish.yml does not name "
            "SHEKEL_IMAGE_DIGEST in the digest emit step; the "
            "operator has to know which env var to set."
        )


class TestGitignoreExcludesCosignKeys:
    """.gitignore excludes cosign private key files so a careless
    ``git add cosign.key`` in the repo root cannot leak the signing
    key.  The PUBLIC key (deploy/cosign.pub) is exempted -- verifiers
    need it.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the full .gitignore source as a string."""
        return GITIGNORE.read_text(encoding="utf-8")

    def test_root_cosign_key_excluded(self, text: str) -> None:
        """``cosign.key`` (root path or any nested path) is ignored."""
        assert re.search(
            r"^cosign\.key\b", text, re.MULTILINE,
        ), (
            ".gitignore does not exclude cosign.key at the repo root; "
            "a stray ``git add`` could leak the signing key (audit "
            "finding F-155 hardening)."
        )

    def test_nested_cosign_keys_excluded(self, text: str) -> None:
        """A glob covers cosign.key files in subdirectories.

        Operators may keep keys under deploy/ or scripts/; the
        gitignore must catch those paths too.
        """
        # ``**/cosign.key`` is the canonical recursive form.  Accept
        # any equivalent that catches subdirectories.
        assert re.search(
            r"^\*\*/cosign\.key\b|^\*\.cosign\.key\b",
            text,
            re.MULTILINE,
        ), (
            ".gitignore does not exclude cosign.key in subdirectories; "
            "a key dropped under deploy/ or scripts/ would track."
        )


class TestDeployReadmeDocumentsRotation:
    """deploy/README.md carries the Image-digest-pinning + Cosign
    rotation procedure.  Operators reading the README must find the
    workflow without context-switching to the audit findings.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        """Return the full deploy README source as a string."""
        return DEPLOY_README.read_text(encoding="utf-8")

    def test_readme_section_exists(self, text: str) -> None:
        """The README has an ``Image Digest Pinning`` section header."""
        assert re.search(
            r"^##\s+Image\s+Digest\s+Pinning",
            text,
            re.MULTILINE | re.IGNORECASE,
        ), (
            "deploy/README.md does not have an ``Image Digest Pinning`` "
            "section; the operator has no documented rotation procedure."
        )

    @pytest.mark.parametrize(
        "marker",
        (
            "SHEKEL_IMAGE_DIGEST",
            "cosign verify",
            "cosign generate-key-pair",
            "deploy/cosign.pub",
            "F-060",
            "F-155",
            "C-36",
        ),
    )
    def test_readme_mentions_marker(self, text: str, marker: str) -> None:
        """Each load-bearing marker the operator needs appears in the
        README.  Catches a partial documentation regression where
        the workflow is renamed but the documentation lags.
        """
        assert marker in text, (
            f"deploy/README.md does not mention {marker!r}; the "
            f"operator workflow documentation is incomplete."
        )
