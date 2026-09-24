"""The test-db image's cache key covers everything that shapes the template.

``scripts/build_test_db_image.py`` bakes ``shekel_test_template`` into a
tagged image so a test run can start a container instead of replaying the
whole migration chain.  The tag is a hash of the inputs, and the tempting
version of that hash -- "the migrations" -- is WRONG in a way that corrupts
results rather than merely slowing them.

``build_test_template._populate_template`` re-applies IN-CODE trigger and
constraint definitions *after* ``alembic upgrade``, deliberately, so the
latest definition wins over the migration-frozen one.  So editing
``app/audit_infrastructure.py``, ``app/posting_infrastructure.py``,
``app/opening_infrastructure/`` or any other module it re-applies changes
the template while ``migrations/`` stays byte-identical.  A migrations-only
key would hand back a stale image and every suite thereafter would run
against the wrong triggers, green.

These tests pin that: each derived input must move the key, and every
``app`` module the builder imports must be covered.  That second assertion
is the one whose absence mattered -- the first version of this module
parametrized over a HAND-COPY of the declared inputs, so it could catch a
removal from that list and never an omission from it, and
``app/append_only_infrastructure.py`` was omitted while these tests sat
green.  They need no docker daemon and no database.

The image's CONTENTS are not asserted here; that is done at bake time by
``_verify_image``, which starts the committed image and refuses it if the
template is missing, unmigrated, stamped at anything but the migration
chain's head, off any exact count ``template_checks`` names, or shipping
audit rows.  Three deliberately-bad images were fed to it and all three
were refused.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts/build_test_db_image.py"


def _load_module():
    """Import the builder by path, without requiring scripts/ on sys.path.

    Returns:
        The imported module object.
    """
    spec = importlib.util.spec_from_file_location("build_test_db_image", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_test_db_image"] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


class TestCacheKeyCoversEveryTemplateInput:
    """Every declared input moves the key, so none can go stale unnoticed."""

    def test_the_key_is_stable_for_an_unchanged_tree(self) -> None:
        """Two calls agree, or the tag would churn on every invocation."""
        assert _MODULE.cache_key() == _MODULE.cache_key()

    def test_every_declared_input_exists(self) -> None:
        """A renamed input must break loudly, not drop out of the hash."""
        for relative in _MODULE.key_inputs():
            assert (_REPO_ROOT / relative).exists(), (
                f"{relative!r} is a derived key input but does not exist; an "
                "input dropped from the key is a silently stale image"
            )

    def test_a_missing_input_raises_rather_than_being_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``cache_key`` refuses a declared-but-absent input.

        That raise is the whole defence against an input quietly leaving the
        hash, and it was untested: replacing it with ``continue`` -- exactly
        the behaviour its own message warns about -- left this module at 10
        passed.

        Args:
            tmp_path: A root holding only the builder, so the key derives
                normally and then finds its inputs absent.
            monkeypatch: Used to repoint the module's repo root.
        """
        # The builder must be PRESENT, or key_inputs() raises first with
        # "cannot derive the key" and the raise under test never runs -- a
        # distinction the first version of this test got wrong.
        builder = tmp_path / "scripts/build_test_template.py"
        builder.parent.mkdir(parents=True)
        builder.write_text(
            (_REPO_ROOT / "scripts/build_test_template.py").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)

        with pytest.raises(_MODULE.BuildError, match="does not exist"):
            _MODULE.cache_key()

    def test_the_builder_s_app_imports_are_all_in_the_key(self) -> None:
        """Every ``app`` module the builder imports contributes to the key.

        This is the assertion whose ABSENCE let the first version of the key
        miss ``app/append_only_infrastructure.py``: the parametrize list was
        a hand-copy of the declared inputs, so it could catch a removal from
        that list and never an omission from it. Reading the builder's
        imports independently is what closes that.
        """
        builder = (
            _REPO_ROOT / "scripts/build_test_template.py"
        ).read_text(encoding="utf-8")
        covered = _MODULE.key_inputs()

        for dotted in set(_MODULE.APP_IMPORT.findall(builder)):
            relative = dotted.replace(".", "/")
            candidates = (relative, f"{relative}.py")
            assert any(
                candidate in covered
                or any(candidate.startswith(f"{c}/") for c in covered)
                for candidate in candidates
            ), (
                f"the builder imports {dotted!r} but nothing in the cache key "
                f"covers it ({covered}); a change to it would reuse a stale "
                "image"
            )

    @pytest.mark.parametrize("relative", _MODULE.key_inputs())
    def test_touching_an_input_changes_the_key(
        self, relative: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Editing any declared input yields a different tag.

        Copies the tree's inputs into a scratch root, points the module at
        it, and appends a comment to one file.  The repository itself is
        never modified.

        Args:
            relative: The declared input to perturb.
            tmp_path: pytest-provided scratch directory.
            monkeypatch: Used to repoint the module's repo root.
        """
        for declared in _MODULE.KEY_INPUTS:
            source = _REPO_ROOT / declared
            target = tmp_path / declared
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                for path in source.rglob("*.py"):
                    copy = target / path.relative_to(source)
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    copy.write_bytes(path.read_bytes())
            else:
                target.write_bytes(source.read_bytes())

        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
        before = _MODULE.cache_key()

        perturbed = tmp_path / relative
        victim = (
            sorted(perturbed.rglob("*.py"))[0]
            if perturbed.is_dir()
            else perturbed
        )
        victim.write_text(
            victim.read_text(encoding="utf-8") + "\n# key probe\n",
            encoding="utf-8",
        )

        assert _MODULE.cache_key() != before, (
            f"editing {victim.relative_to(tmp_path)} did not change the cache "
            f"key, so a change to {relative!r} would reuse a stale image"
        )


class TestMigrationHead:
    """The head the baked image is verified against is well defined."""

    def test_the_chain_has_exactly_one_head(self) -> None:
        """Two heads mean the schema to verify against is ambiguous."""
        head = _MODULE.migration_head()

        assert head, "no migration head found"
        assert len(head) >= 8, f"implausible revision id {head!r}"

    def test_the_head_is_not_any_revision_s_parent(self) -> None:
        """Independent re-derivation: nothing may descend from the head.

        Computed a different way from the function under test -- by grepping
        for the head as a ``down_revision`` -- so the two would have to be
        wrong together.
        """
        head = _MODULE.migration_head()
        versions = _REPO_ROOT / "migrations/versions"

        children = [
            path.name
            for path in versions.glob("*.py")
            if f'down_revision = "{head}"' in path.read_text(encoding="utf-8")
            or f"down_revision = '{head}'" in path.read_text(encoding="utf-8")
        ]

        assert not children, (
            f"{head} is reported as the head but {children} descend from it"
        )


class TestADockerFaultIsNotAVerdictAboutTheImage:
    """A container that never started says NOTHING about the baked image.

    ``main`` verifies a cached image on every invocation and rebuilds when
    verification rejects it.  Both outcomes arrived as ``BuildError``, so a
    ``docker run`` that failed for a reason having nothing to do with the
    image -- on the rootless daemon, a host-port bind that loses a race with
    an outbound connection about 30% of the time -- was reported as "cached
    image rejected" and triggered a 9 s rebuild, every invocation, with the
    real error buried in the rebuild's output.  The cache never served its
    purpose and nothing said so.
    """

    def test_a_failed_command_raises_the_infrastructure_class(self) -> None:
        """``_run`` only ever runs docker, so its failures are faults.

        Driven through the interpreter rather than ``docker`` so the
        assertion is about the raise TYPE and holds where no daemon exists.
        """
        with pytest.raises(_MODULE.DockerError):
            # Pylint: ``protected-access`` -- ``_run`` is the builder's private
            # helper and the unit under test here: its raise TYPE is the
            # property, and no public entry point reaches it without docker.
            # pylint: disable=protected-access
            _MODULE._run([sys.executable, "-c", "raise SystemExit(3)"])

    def test_the_fault_class_is_still_a_build_error(self) -> None:
        """``main``'s outer handler must keep reporting it and exiting 1."""
        assert issubclass(_MODULE.DockerError, _MODULE.BuildError)

    def test_main_does_not_rebuild_when_the_container_would_not_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The defect: an unreachable daemon read as a stale image."""
        rebuilt: list[str] = []
        monkeypatch.setattr(_MODULE, "image_tag", lambda: "shekel-test-db:deadbeef")
        monkeypatch.setattr(_MODULE, "image_exists", lambda tag: True)
        monkeypatch.setattr(_MODULE, "build", rebuilt.append)

        def _no_start(tag: str) -> None:
            """Fail the way a refused container start does."""
            raise _MODULE.DockerError("docker run -d... exited 125: bind: address already in use")

        monkeypatch.setattr(_MODULE, "_verify_image", _no_start)

        assert _MODULE.main([]) == 1
        assert not rebuilt, (
            "rebuilt on an infrastructure fault; a container that never "
            "started is not evidence that the image is stale"
        )

    def test_main_still_rebuilds_on_a_real_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The carve-out must not disarm the rebuild it carves out of.

        Without this arm the test above passes on code that never rebuilds
        at all, which would silently un-fix the poisoned-tag defect
        ``main``'s verify-on-every-invocation exists for.
        """
        rebuilt: list[str] = []
        monkeypatch.setattr(_MODULE, "image_tag", lambda: "shekel-test-db:deadbeef")
        monkeypatch.setattr(_MODULE, "image_exists", lambda tag: True)
        monkeypatch.setattr(_MODULE, "build", rebuilt.append)
        monkeypatch.setattr(_MODULE, "_run", lambda *a, **k: None)

        def _stale(tag: str) -> None:
            """Fail the way a genuinely stale image does."""
            raise _MODULE.BuildError(f"{tag} has no template database (got '0')")

        monkeypatch.setattr(_MODULE, "_verify_image", _stale)

        assert _MODULE.main([]) == 0
        assert rebuilt == ["shekel-test-db:deadbeef"]


class TestTheBuilderLeavesNothingBehind:
    """Every container the builder starts is removed WITH its volume.

    The baked image inherits the base image's ``VOLUME`` declaration, so a
    ``docker rm -f`` without ``-v`` leaks one anonymous volume per container.
    Measured on the rootless daemon: 0 -> 1 -> 4 volumes across two suite
    invocations, unbounded, on a daemon whose metadata nothing prunes.
    """

    def test_every_removal_takes_the_volume_with_it(self) -> None:
        """A bare ``rm -f`` anywhere in the builder re-opens the leak."""
        source = _SCRIPT.read_text(encoding="utf-8")

        bare = [
            line.strip()
            for line in source.splitlines()
            if '"docker", "rm", "-f"' in line
        ]

        assert not bare, f"these removals drop the anonymous volume: {bare}"

    @pytest.mark.parametrize("function", ["_verify_image", "build"])
    def test_the_container_start_is_inside_the_cleanup_try(
        self, function: str
    ) -> None:
        """A start that fails after CREATE must still be cleaned up.

        ``docker run`` sat outside the ``try`` in both helpers, so a refused
        start left the container behind in ``Created`` state -- four of them
        accumulated during one session of probing the rootless daemon.
        """
        tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
        target = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == function
        )

        guarded = {
            id(inner)
            for node in ast.walk(target)
            if isinstance(node, ast.Try)
            for inner in ast.walk(node)
        }
        runs = [
            node
            for node in ast.walk(target)
            if isinstance(node, ast.Constant) and node.value == "run"
        ]

        assert runs, f"no docker run found in {function}"
        unguarded = [node for node in runs if id(node) not in guarded]
        assert not unguarded, (
            f"{function}'s docker run is outside the try that removes the "
            "container, so a failed start leaks it"
        )


class TestTheFaultVerdictLineHoldsAtEverySite:
    """``DockerError`` only helps where the handler was taught to see it.

    An adversarial review found the class introduced, ``main`` updated, and
    the two OTHER places that draw the same line left behind: ``build``'s
    post-verify handler, which untagged a correctly built image on a fault,
    and ``ask``, which runs SQL through ``_run`` and so reported a VERDICT as
    a fault. Each is the introduced class's own defect, at a site it missed.
    """

    def test_a_fault_after_the_commit_does_not_discard_the_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ~9 s bake must survive a lost port-bind race in verification.

        ``build`` untagged on any ``BuildError``, and ``DockerError`` is one,
        so a container that never started threw away an image that had just
        been committed correctly -- the very confusion the class exists to
        prevent.
        """
        removed: list[list[str]] = []

        def _record(command: list[str], **kwargs: object) -> object:
            """Record docker invocations without running any."""
            removed.append(command)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(_MODULE, "_run", _record)
        monkeypatch.setattr(_MODULE, "_wait_ready", lambda container: None)
        monkeypatch.setattr(_MODULE, "_wait_stopped", lambda container: None)
        monkeypatch.setattr(_MODULE, "_mapped_port", lambda container: 5432)
        monkeypatch.setattr(
            _MODULE.subprocess,
            "run",
            lambda *a, **k: type(
                "P", (), {"returncode": 0, "stdout": "", "stderr": ""}
            )(),
        )

        def _no_start(tag: str) -> None:
            """Fail the way a refused container start does."""
            raise _MODULE.DockerError("docker run -d... exited 125: bind")

        monkeypatch.setattr(_MODULE, "_verify_image", _no_start)

        with pytest.raises(_MODULE.DockerError):
            _MODULE.build("shekel-test-db:deadbeef")

        # `build` legitimately untags a PREDECESSOR before committing, so the
        # property is ordering: nothing may untag the tag AFTER the commit
        # that created it.  Asserting on the bare presence of `rmi` failed
        # here against correct code, which is how this got written properly.
        commits = [i for i, cmd in enumerate(removed) if cmd[1] == "commit"]
        assert commits, f"nothing was committed; docker saw: {removed}"
        after_commit = [
            cmd for cmd in removed[commits[0] + 1:] if cmd[:2] == ["docker", "rmi"]
        ]
        assert not after_commit, (
            "a correctly committed image was discarded because a container "
            f"would not start: {after_commit}"
        )

    def test_a_failed_query_is_a_verdict_so_the_image_is_rebuilt(self) -> None:
        """A missing relation says the IMAGE is wrong, not the daemon.

        ``ask`` runs `docker exec psql` through ``_run``, which classifies
        every non-zero exit as a fault. `docker exec` exits non-zero both for
        an unreachable daemon and for `relation ... does not exist`, so the
        image that used to be rebuilt would instead have reported an error
        and stopped.
        """
        source = _SCRIPT.read_text(encoding="utf-8")
        start = source.index("        def ask(")
        end = source.index("        present = ask(")
        body = source[start:end]

        assert "check=False" in body, (
            "ask() lets _run raise, so a SQL failure is classified as an "
            "infrastructure fault and no longer triggers a rebuild"
        )
        assert "raise BuildError(" in body, (
            "ask() does not re-raise a failed query as a verdict"
        )


class TestAFailedTemplateBuildReportsBothStreams:
    """Ruling R-BAL121: the failure report quotes the builder's log AND its error.

    The template builder runs the migration chain through the deploy's own
    runner (``app.migration_runner``), so ``migrations/env.py`` leaves its
    logging to the app: each revision is logged as JSON on STDOUT and the
    traceback goes to stderr.  A report quoting stderr alone would drop the
    line naming the revision that was running.
    """

    def test_the_report_quotes_the_log_tail_and_the_error(self):
        """The last revisions logged and the traceback both appear; older lines do not."""
        log = "\n".join(
            f'{{"message": "Running upgrade r{i} -> r{i + 1}"}}'
            for i in range(100)
        )
        report = _MODULE._builder_failure(log, "Traceback: forged failure\n")

        tail = _MODULE._FAILED_BUILD_LOG_LINES
        assert "Running upgrade r99 -> r100" in report
        assert f"Running upgrade r{100 - tail} -> r{101 - tail}" in report
        assert f"Running upgrade r{99 - tail} -> r{100 - tail}" not in report
        assert report.rstrip().endswith("Traceback: forged failure")


class TestTheTemplateBuilderStartsUnderThePinnedLocale:
    """The scrubbed environment carries ``LC_ALL`` -- when there is one.

    ``build`` runs ``build_test_template.py`` with only ``PATH``, ``HOME`` and
    the admin DSN, and that builder calls ``create_app``, which refuses to
    start without the pinned locale (plan step ``recurrence:R12``, ruling
    ``R-R92``).  ``scripts/test.sh`` exports it, so a suite run that bakes the
    image proves the pass-through arm -- every CI run does, since its runner
    caches no image -- while a local run whose image is already built never
    reaches ``build``.  No run proves the OTHER arm: a hand run from a shell
    without ``LC_ALL`` must leave the variable ABSENT, not empty, because an
    empty value stops the child's ``load_dotenv()`` taking it from a host
    ``.env`` (dotenv never overrides a variable that is already set).
    """

    @staticmethod
    def _builder_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
        """Run ``build`` with docker stubbed out; return the builder's env.

        The builder is made to fail, which stops ``build`` right after the
        one call under test.

        Args:
            monkeypatch: Used to stub docker and capture the subprocess call.

        Returns:
            The environment ``build`` handed ``build_test_template.py``.
        """
        seen: dict[str, str] = {}

        def _capture(*_args: object, **kwargs: object) -> object:
            """Record the builder's environment, then fail the build."""
            seen.update(kwargs["env"])
            return type(
                "P", (), {"returncode": 1, "stdout": "", "stderr": "stopped"}
            )()

        monkeypatch.setattr(
            _MODULE,
            "_run",
            lambda *a, **k: type(
                "R", (), {"returncode": 0, "stdout": "", "stderr": ""}
            )(),
        )
        monkeypatch.setattr(_MODULE, "_wait_ready", lambda container: None)
        monkeypatch.setattr(_MODULE, "_mapped_port", lambda container: 5432)
        monkeypatch.setattr(_MODULE.subprocess, "run", _capture)
        with pytest.raises(
            _MODULE.BuildError, match="build_test_template.py failed"
        ):
            _MODULE.build("shekel-test-db:deadbeef")
        return seen

    def test_the_parent_s_value_is_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The parent's own value reaches the builder, whatever it is.

        A sentinel rather than the pin, so a builder that wrote the pin
        itself whenever the parent had any value would fail here.
        """
        monkeypatch.setenv("LC_ALL", "xx_XX.sentinel")
        assert self._builder_env(monkeypatch)["LC_ALL"] == "xx_XX.sentinel"

    def test_an_absent_value_stays_absent_rather_than_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A shell with no ``LC_ALL`` leaves the child free to read ``.env``."""
        monkeypatch.delenv("LC_ALL", raising=False)
        assert "LC_ALL" not in self._builder_env(monkeypatch)
