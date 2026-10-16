"""The backup scripts encrypt with ONE private key file, or refuse.

Plan step **balance:X-do**, three of Josh's answers (2026-10-16) and one
finding:

* **R-BAL304**: ``scripts/backup.sh`` with no usable key and no explicit
  plaintext choice REFUSES and writes nothing; ``--no-encrypt`` is the choice;
  ``scripts/deploy.sh``'s pre-deploy backup refuses the same way (a missing or
  non-executable ``backup.sh`` included), or the operator passes
  ``--skip-backup``.
* **R-BAL305**: ``backup.sh``, ``restore.sh`` and ``verify_backup.sh`` read the
  passphrase from one file at a fixed path, refused unless it is private; the
  ``BACKUP_ENCRYPTION_PASSPHRASE`` variable is gone, and an install still
  setting it is refused, naming the file.
* **R-BAL306**: ``restore.sh``'s pre-restore safety copy is encrypted with
  that file, or ``restore.sh`` stops before touching anything, unless
  ``--plaintext-safety-dump`` or ``--skip-safety-dump``.
* **BAL-663**: every artifact is created ``0600`` and every directory the
  scripts create ``0700``, whatever the caller's umask.

**No test reads the real key path.**  Each run that reaches the key sources
``scripts/_backup_lib.sh`` and then points ``SHEKEL_BACKUP_KEY_FILE`` at a
file in the test's own directory.  ``backup.sh`` runs whole that way, against
a ``docker`` stub, into a directory of the test's; ``restore.sh`` and
``verify_backup.sh`` are NEVER run whole: their ``main`` runs with every step
a recorder, ``restore.sh``'s safety copy excepted (it writes only into the
test's directory, through the stub).  Every gpg run uses a private
``GNUPGHOME`` whose agent can never open a pinentry dialog, and the agent the
encryption starts is stopped after each test.
"""
from __future__ import annotations

import gzip
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

from tests.test_scripts._shell_harness import function_text, run_bash

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LIB = _REPO_ROOT / "scripts" / "_backup_lib.sh"
_BACKUP = _REPO_ROOT / "scripts" / "backup.sh"
_RESTORE = _REPO_ROOT / "scripts" / "restore.sh"
_VERIFY = _REPO_ROOT / "scripts" / "verify_backup.sh"
_DEPLOY = _REPO_ROOT / "scripts" / "deploy.sh"

#: The line each backup script loads the lib with, which the harness replaces
#: with an absolute load followed by the test's own key path.
_SOURCE_LINE = 'source "$(dirname "${BASH_SOURCE[0]}")/_backup_lib.sh"'

_GPG = shutil.which("gpg")
_GPGCONF = shutil.which("gpgconf")

#: What the ``docker`` stub's ``pg_dump`` prints: the "database".
_DUMP = "-- probe dump\nCREATE TABLE probe (id int);\n"
_RETIRED = "BACKUP_ENCRYPTION_PASSPHRASE"
_SECRET = "probe-secret-value"

_RESTORE_STEPS = (
    "check_prerequisites", "validate_backup_artifact", "confirm_restore",
    "stop_app", "drop_and_recreate_database", "restore_backup", "start_app",
    "verify_restore",
)
_VERIFY_STEPS = (
    "check_prerequisites", "create_temp_database", "restore_to_temp",
    "run_sanity_checks", "run_integrity_checks", "cleanup",
)


@pytest.fixture(name="gnupg_home")
def fixture_gnupg_home():
    """A private GNUPGHOME whose agent answers every pinentry request "no".

    A short path under the system temp directory, because gpg puts its agent
    socket in the home when the runtime directory is absent, and a socket
    path is capped near 108 bytes.  The agent an encryption starts is
    stopped afterwards.
    """
    assert _GPG and _GPGCONF, "gpg and gpgconf must be installed"
    home = Path(tempfile.mkdtemp(prefix="shekel-gpg-"))
    home.chmod(0o700)
    (home / "gpg-agent.conf").write_text(
        "pinentry-program /usr/bin/false\n", encoding="utf-8")
    yield home
    env = {"PATH": "/usr/bin:/bin", "GNUPGHOME": str(home)}
    for args in (["--kill", "gpg-agent"], ["--remove-socketdir"]):
        subprocess.run([_GPGCONF, *args], env=env, capture_output=True,
                       timeout=30, check=False)
    shutil.rmtree(home)


def _key(tmp_path: Path, text: str = "probe-passphrase\n",
         mode: int = 0o600) -> Path:
    """Write a key file holding *text* with *mode*, owned by this user."""
    key = tmp_path / "backup-passphrase"
    key.write_text(text, encoding="utf-8")
    key.chmod(mode)
    return key


def _errors(output: str) -> list[str]:
    """Return the ``[ERROR]`` lines of a script's log."""
    return [line for line in output.splitlines() if "[ERROR]" in line]


def _mode(path: Path) -> int:
    """Return *path*'s permission bits."""
    return stat.S_IMODE(path.stat().st_mode)


def _decrypt(artifact: Path, key: Path, gnupg_home: Path) -> str:
    """Decrypt and decompress *artifact* with *key*, as text."""
    out = subprocess.run(
        [_GPG, "--batch", "--quiet", "--no-autostart", "--passphrase-file",
         str(key), "-d", str(artifact)],
        capture_output=True, timeout=30, check=True,
        env={"PATH": "/usr/bin:/bin", "GNUPGHOME": str(gnupg_home)},
    )
    return gzip.decompress(out.stdout).decode("utf-8")


def _stubs(tmp_path: Path, *, watch: Path, dump_status: int = 0,
           gpg_fails: bool = False, interrupt: bool = False) -> Path:
    """Install a ``docker`` stub and a ``gpg`` wrapper; return their directory.

    The ``docker`` stub answers ``inspect`` with ``true`` (the container is
    running) and ``exec`` with :data:`_DUMP`, then exits *dump_status*: a
    non-zero status AFTER output is a pg_dump that died mid-stream.  With
    *interrupt* it then waits for the partial file to appear in *watch* and
    sends SIGTERM to the shell running the dump (its parent), as an operator's
    Ctrl-C or a ``kill`` would.  The ``gpg`` wrapper records what *watch*
    holds at the moment gpg runs, then runs the real gpg, or reads its input
    and exits 2 when *gpg_fails*.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "dump.sql").write_text(_DUMP, encoding="utf-8")
    interruption = textwrap.dedent(f"""\
        for _ in $(seq 200); do
                ls -A '{watch}' 2>/dev/null | grep -q '[.]partial$' && break
                sleep 0.05
            done
            kill -TERM "$PPID"; sleep 0.5""") if interrupt else ":"
    docker = bin_dir / "docker"
    docker.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >>'{bin_dir / "docker.log"}'
        case "$1" in
            inspect) echo true ;;
            exec)
                cat '{bin_dir / "dump.sql"}'
                {interruption}
                exit {dump_status} ;;
            *) exit 99 ;;
        esac
        """), encoding="utf-8")
    gpg_tail = "cat >/dev/null; exit 2" if gpg_fails else f'exec {_GPG} "$@"'
    gpg = bin_dir / "gpg"
    gpg.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        ls -A '{watch}' >>'{bin_dir / "during_gpg.log"}'
        {gpg_tail}
        """), encoding="utf-8")
    for stub in (docker, gpg):
        stub.chmod(0o755)
    return bin_dir


def _run_backup(tmp_path: Path, gnupg_home: Path, *args: str,
                key: Path | None = None, local: Path | None = None,
                nas: Path | None = None, env: str = "", dump_status: int = 0,
                gpg_fails: bool = False):
    """Run ``backup.sh`` whole against the stubs, local-only unless *nas*.

    The script's own text runs, with its lib load replaced by an absolute one
    and the key path pointed at *key* (a path that does not exist when
    None), under umask 022 so the modes it creates are its own doing.

    Returns:
        The completed process and the local backup directory.
    """
    local = local or tmp_path / "backups"
    bin_dir = _stubs(tmp_path, watch=local, dump_status=dump_status,
                     gpg_fails=gpg_fails)
    script = _BACKUP.read_text(encoding="utf-8")
    assert script.count(_SOURCE_LINE + "\n") == 1, "the lib load moved"
    assert script.endswith('\nmain "$@"\n'), "the script's last line moved"
    body = script.replace(
        _SOURCE_LINE, 'source "$LIB"; SHEKEL_BACKUP_KEY_FILE="$KEY"', 1,
    )[:-len('main "$@"\n')]
    result = run_bash(
        'LIB="$1"; KEY="$2"; export GNUPGHOME="$3"; umask 022; '
        f'export DB_CONTAINER=probe-db; {env}\n'
        'eval "$4"; shift 4; main "$@"',
        str(_LIB), str(key or tmp_path / "absent-key"), str(gnupg_home),
        body, "--local-dir", str(local),
        *(("--nas-dir", str(nas)) if nas else ("--no-nas",)), *args,
        path_prefix=bin_dir,
    )
    return result, local


def _recorders(steps: tuple[str, ...]) -> str:
    """Bash definitions replacing each of *steps* by an appender to ``$STEPS``."""
    return "\n".join(f'{step}() {{ echo {step} >>"$STEPS"; }}'
                     for step in steps)


def _taken(tmp_path: Path) -> list[str]:
    """Return the recorded steps, in order."""
    steps = tmp_path / "steps"
    return (steps.read_text(encoding="utf-8").splitlines()
            if steps.exists() else [])


def _run_restore(tmp_path: Path, gnupg_home: Path, *args: str,
                 key: Path | None = None, env: str = "",
                 dump_status: int = 0, interrupt: bool = False):
    """Run ``restore.sh``'s ``main`` with every step a recorder.

    Every step that reads or changes the database is a recorder; the safety
    copy is the real ``take_safety_dump`` writing into the test's directory
    through the ``docker`` stub.  ``app_container_exists`` answers "no".

    Returns:
        The completed process and the safety copy's directory.
    """
    dumps = tmp_path / "safety"
    bin_dir = _stubs(tmp_path, watch=dumps, dump_status=dump_status,
                     interrupt=interrupt)
    functions = "\n".join(function_text(_RESTORE, name) for name in
                          ("usage", "take_safety_dump", "main"))
    result = run_bash(
        'LIB="$1"; KEY="$2"; export GNUPGHOME="$3"; STEPS="$4"; '
        'BACKUP_LOCAL_DIR="$5"; umask 022; ' + env + '\n'
        'source "$LIB"; SHEKEL_BACKUP_KEY_FILE="$KEY"\n'
        'eval "$6"; app_container_exists() { return 1; }\n'
        'DB_CONTAINER=probe-db; APP_CONTAINER=probe-app; PGUSER=probe; '
        'PGDATABASE=probe; DESTRUCTIVE_WINDOW=false; SAFETY_DUMP_PATH=""; '
        'SAFETY_DUMP_PARTIAL=""\n'
        'eval "$7"; shift 7; main "$@"',
        str(_LIB), str(key or tmp_path / "absent-key"), str(gnupg_home),
        str(tmp_path / "steps"), str(dumps), _recorders(_RESTORE_STEPS),
        functions, *args,
        path_prefix=bin_dir,
    )
    return result, dumps


def _backup_file(tmp_path: Path, name: str) -> Path:
    """Write a stand-in backup artifact; no recorder ever reads it."""
    path = tmp_path / name
    path.write_bytes(b"not read")
    return path


def _run_verify(tmp_path: Path, *args: str, key: Path | None = None,
                env: str = ""):
    """Run ``verify_backup.sh``'s ``main`` with every step a recorder."""
    functions = "\n".join(function_text(_VERIFY, name)
                          for name in ("usage", "main"))
    return run_bash(
        'LIB="$1"; KEY="$2"; STEPS="$3"; VERIFY_DB=probe_verify; ' + env + '\n'
        'source "$LIB"; SHEKEL_BACKUP_KEY_FILE="$KEY"\n'
        'eval "$4"; eval "$5"; shift 5; main "$@"',
        str(_LIB), str(key or tmp_path / "absent-key"),
        str(tmp_path / "steps"), _recorders(_VERIFY_STEPS), functions, *args,
        path_prefix=tmp_path,
    )


def _require_key(key: Path):
    """Run the lib's ``require_backup_key`` against *key*."""
    return run_bash(
        'source "$1"; SHEKEL_BACKUP_KEY_FILE="$2"; require_backup_key; '
        'echo accepted',
        str(_LIB), str(key),
    )


class TestTheKeyFileIsUsedOnlyWhenPrivate:
    """``require_backup_key``: a private regular file with a first line, or exit 1."""

    @pytest.mark.parametrize(("text", "mode"), [
        ("probe-passphrase\n", 0o600), ("probe-passphrase\n", 0o400),
        ("no-newline-at-end", 0o600), ("first\nsecond\n", 0o600),
    ], ids=["0600", "0400", "no-final-newline", "two-lines"])
    def test_a_private_file_with_a_first_line_is_accepted(
            self, tmp_path, text, mode):
        """What gpg reads (the first line) is there, and only its owner may read it."""
        result = _require_key(_key(tmp_path, text, mode))
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines() == ["accepted"]

    def _refused(self, key: Path, problem: str) -> None:
        """Assert *key* is refused, the ERROR lines naming it and *problem*."""
        result = _require_key(key)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "accepted" not in result.stdout
        errors = _errors(result.stdout)
        assert any(str(key) in line and problem in line
                   for line in errors), errors

    def test_no_setting_moves_the_key_path(self, tmp_path):
        """An exported SHEKEL_BACKUP_KEY_FILE is overwritten by the lib's own path.

        Compared with the injected value, never with the path the lib spells.
        """
        decoy = tmp_path / "decoy-key"
        result = run_bash(
            'export SHEKEL_BACKUP_KEY_FILE="$2"; source "$1"; '
            'printf "%s" "$SHEKEL_BACKUP_KEY_FILE"',
            str(_LIB), str(decoy),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout and result.stdout != str(decoy), result.stdout

    def test_a_missing_file_is_refused(self, tmp_path):
        """No file: the refusal names the path to write."""
        self._refused(tmp_path / "backup-passphrase", "does not exist")

    def test_a_symbolic_link_is_refused_even_to_a_private_file(self, tmp_path):
        """By lstat: the link is judged, never what it points at."""
        target = _key(tmp_path)
        link = tmp_path / "link"
        link.symlink_to(target)
        self._refused(link, "symbolic link")

    def test_a_dangling_symbolic_link_is_refused_as_a_link(self, tmp_path):
        """A link to nothing is named a link, not a missing file."""
        link = tmp_path / "link"
        link.symlink_to(tmp_path / "nowhere")
        self._refused(link, "symbolic link")

    def test_a_directory_is_refused(self, tmp_path):
        """Only a regular file is a key file."""
        directory = tmp_path / "backup-passphrase"
        directory.mkdir(mode=0o700)
        self._refused(directory, "not a regular file")

    def test_a_file_owned_by_another_user_is_refused(self):
        """``/etc/passwd`` belongs to root; the suite never runs as root (CI: runner)."""
        foreign = Path("/etc/passwd")
        assert foreign.stat().st_uid != os.geteuid(), (
            "the suite runs as /etc/passwd's owner; this case needs a file "
            "owned by someone else")
        self._refused(foreign, "not owned")

    @pytest.mark.parametrize("mode", [0o640, 0o620, 0o610, 0o604, 0o602,
                                      0o601, 0o660, 0o644])
    def test_any_group_or_other_permission_bit_is_refused(self, tmp_path,
                                                          mode):
        """Read, write or execute, for the group or for others: refused."""
        self._refused(_key(tmp_path, mode=mode), "group or other users")

    @pytest.mark.parametrize("text", ["", "\n", "\nsecond-line\n"],
                             ids=["empty", "newline-only",
                                  "empty-first-line"])
    def test_an_empty_first_line_is_refused(self, tmp_path, text):
        """gpg reads the FIRST line: an empty one is no passphrase."""
        self._refused(_key(tmp_path, text), "first line is empty")

    def test_a_file_its_owner_cannot_read_is_refused(self, tmp_path):
        """Mode 0200 is private yet unreadable; refused before gpg meets it."""
        assert os.geteuid() != 0, "root reads any file; this case needs a non-root run"
        self._refused(_key(tmp_path, mode=0o200), "cannot be read")


class TestTheRetiredVariableIsRefusedFirst:
    """Each of the three scripts refuses ``BACKUP_ENCRYPTION_PASSPHRASE`` before anything."""

    @staticmethod
    def _assert_refused_naming_the_file(result, key: Path) -> None:
        """Exit 1, the ERROR lines name the variable and the file, never the value."""
        assert result.returncode == 1, result.stdout + result.stderr
        errors = _errors(result.stdout)
        assert any(f"{_RETIRED} is set" in line for line in errors), errors
        assert any(str(key) in line for line in errors), errors
        assert _SECRET not in result.stdout + result.stderr

    @pytest.mark.parametrize("script", ["backup", "restore", "verify"])
    def test_even_help_is_refused_while_the_variable_is_set(
            self, tmp_path, gnupg_home, script):
        """The refusal is main's first act: no argument is read, no usage printed."""
        key = _key(tmp_path)
        env = f"export {_RETIRED}='';"
        if script == "backup":
            result, _ = _run_backup(tmp_path, gnupg_home, "--help", key=key,
                                    env=env)
        elif script == "restore":
            result, _ = _run_restore(tmp_path, gnupg_home, "--help", key=key,
                                     env=env)
        else:
            result = _run_verify(tmp_path, "--help", key=key, env=env)
        self._assert_refused_naming_the_file(result, key)
        assert "Usage:" not in result.stdout + result.stderr

    @pytest.mark.parametrize("value", [_SECRET, ""], ids=["set", "set-empty"])
    @pytest.mark.parametrize("args", [(), ("--no-encrypt",)],
                             ids=["default", "no-encrypt"])
    def test_backup_refuses_and_writes_nothing(self, tmp_path, gnupg_home,
                                               value, args):
        """Even with a usable key, and even when a plaintext backup was chosen."""
        key = _key(tmp_path)
        result, local = _run_backup(
            tmp_path, gnupg_home, *args, key=key,
            env=f"export {_RETIRED}='{value}';",
        )
        self._assert_refused_naming_the_file(result, key)
        assert not local.exists() or not any(local.iterdir())
        assert not (tmp_path / "bin" / "docker.log").exists()

    @pytest.mark.parametrize("value", [_SECRET, ""], ids=["set", "set-empty"])
    def test_restore_refuses_before_any_step(self, tmp_path, gnupg_home,
                                             value):
        """No step runs: nothing validated, confirmed, dumped or dropped."""
        key = _key(tmp_path)
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz.gpg")
        result, dumps = _run_restore(
            tmp_path, gnupg_home, "--skip-confirm", str(backup), key=key,
            env=f"export {_RETIRED}='{value}';",
        )
        self._assert_refused_naming_the_file(result, key)
        assert _taken(tmp_path) == []
        assert not dumps.exists()

    @pytest.mark.parametrize("value", [_SECRET, ""], ids=["set", "set-empty"])
    def test_verify_refuses_before_any_step(self, tmp_path, value):
        """No temporary database is created."""
        key = _key(tmp_path)
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz.gpg")
        result = _run_verify(tmp_path, str(backup), key=key,
                             env=f"export {_RETIRED}='{value}';")
        self._assert_refused_naming_the_file(result, key)
        assert _taken(tmp_path) == []


class TestBackupIsEncryptedUnlessPlaintextIsChosen:
    """``backup.sh``: encrypted with the key file, ``--no-encrypt``, or refused."""

    def test_without_a_usable_key_it_refuses_and_touches_nothing(
            self, tmp_path, gnupg_home):
        """No artifact, no directory, no docker call; the fix names both ways out."""
        result, local = _run_backup(tmp_path, gnupg_home)
        assert result.returncode == 1, result.stdout + result.stderr
        errors = _errors(result.stdout)
        assert any(str(tmp_path / "absent-key") in line for line in errors), (
            errors)
        assert any("--no-encrypt" in line for line in errors), errors
        assert not local.exists()
        assert not (tmp_path / "bin" / "docker.log").exists()

    def test_with_a_usable_key_only_an_encrypted_artifact_is_written(
            self, tmp_path, gnupg_home):
        """One ``.sql.gz.gpg``, decrypting to the dump; no plaintext beside it, ever.

        While gpg runs, the directory holds at most the partial file, a
        dotfile the ``shekel_backup_*`` globs of retention and the verify
        cron line cannot select.
        """
        key = _key(tmp_path)
        result, local = _run_backup(tmp_path, gnupg_home, key=key)
        assert result.returncode == 0, result.stdout + result.stderr
        artifacts = sorted(local.iterdir())
        assert len(artifacts) == 1, artifacts
        assert artifacts[0].name.startswith("shekel_backup_")
        assert artifacts[0].name.endswith(".sql.gz.gpg")
        assert _decrypt(artifacts[0], key, gnupg_home) == _DUMP
        during = (tmp_path / "bin" / "during_gpg.log").read_text(
            encoding="utf-8").split()
        assert during and all(name.startswith(".") for name in during), during

    def test_no_encrypt_without_a_key_writes_plaintext_and_says_so(
            self, tmp_path, gnupg_home):
        """The explicit choice: a ``.sql.gz``, one INFO line, no WARNING."""
        result, local = _run_backup(tmp_path, gnupg_home, "--no-encrypt")
        assert result.returncode == 0, result.stdout + result.stderr
        artifacts = sorted(local.iterdir())
        assert [p.name.endswith(".sql.gz") for p in artifacts] == [True]
        assert gzip.decompress(artifacts[0].read_bytes()).decode() == _DUMP
        assert "[WARNING]" not in result.stdout
        assert any("--no-encrypt" in line and "[INFO]" in line
                   for line in result.stdout.splitlines()), result.stdout

    def test_no_encrypt_wins_over_a_key_file_with_one_warning(
            self, tmp_path, gnupg_home):
        """A key file is there, yet the operator chose plaintext: said once, loudly."""
        key = _key(tmp_path)
        result, local = _run_backup(tmp_path, gnupg_home, "--no-encrypt",
                                    key=key)
        assert result.returncode == 0, result.stdout + result.stderr
        assert [p.name.endswith(".sql.gz") for p in local.iterdir()] == [True]
        warnings = [line for line in result.stdout.splitlines()
                    if "[WARNING]" in line]
        assert len(warnings) == 1, warnings
        assert str(key) in warnings[0] and "--no-encrypt" in warnings[0]

    def test_the_retired_encrypt_flag_is_an_unknown_option(self, tmp_path,
                                                           gnupg_home):
        """``--encrypt`` claimed a choice that no longer exists."""
        result, _ = _run_backup(tmp_path, gnupg_home, "--encrypt",
                                key=_key(tmp_path))
        assert result.returncode == 1, result.stdout + result.stderr
        assert any("Unknown option: --encrypt" in line
                   for line in _errors(result.stdout)), result.stdout

    @pytest.mark.parametrize("args", [(), ("--no-encrypt",)],
                             ids=["encrypted", "plaintext"])
    def test_a_pg_dump_that_dies_mid_stream_leaves_nothing(
            self, tmp_path, gnupg_home, args):
        """gzip and gpg exit 0 on the shortened stream; pg_dump's status decides."""
        result, local = _run_backup(tmp_path, gnupg_home, *args,
                                    key=_key(tmp_path), dump_status=1)
        assert result.returncode == 1, result.stdout + result.stderr
        assert list(local.iterdir()) == []
        assert any("pg_dump exited 1" in line
                   for line in _errors(result.stdout)), result.stdout

    def test_the_writer_checks_every_stage_whatever_the_caller_s_options(
            self, tmp_path):
        """pipefail is the writer's own: a caller without it still gets nothing."""
        local = tmp_path / "backups"
        bin_dir = _stubs(tmp_path, watch=local, dump_status=1)
        result = run_bash(
            'set +o pipefail; source "$1"; '
            'DB_CONTAINER=probe-db; PGUSER=probe; PGDATABASE=probe; '
            'write_dump_artifact "$2" "probe advice"',
            str(_LIB), str(local / "shekel_backup_20260101_000000.sql.gz"),
            path_prefix=bin_dir,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert list(local.iterdir()) == []
        assert any("pg_dump exited 1" in line
                   for line in _errors(result.stdout)), result.stdout

    def test_a_failed_encryption_leaves_no_plaintext_behind(self, tmp_path,
                                                            gnupg_home):
        """Today's "unencrypted backup retained" path is gone: nothing is kept."""
        result, local = _run_backup(tmp_path, gnupg_home, key=_key(tmp_path),
                                    gpg_fails=True)
        assert result.returncode == 1, result.stdout + result.stderr
        assert list(local.iterdir()) == []

    def test_artifacts_are_private_whatever_the_umask(self, tmp_path,
                                                      gnupg_home):
        """BAL-663: the artifact 0600, each directory the script made 0700."""
        local = tmp_path / "new" / "backups"
        result, _ = _run_backup(tmp_path, gnupg_home, key=_key(tmp_path),
                                local=local)
        assert result.returncode == 0, result.stdout + result.stderr
        assert [_mode(p) for p in local.iterdir()] == [0o600]
        assert (_mode(local), _mode(local.parent)) == (0o700, 0o700)

    def test_an_existing_directory_keeps_its_mode(self, tmp_path, gnupg_home):
        """Only what the script creates is made private; it re-modes nothing."""
        local = tmp_path / "backups"
        local.mkdir(mode=0o755)
        local.chmod(0o755)
        result, _ = _run_backup(tmp_path, gnupg_home, "--no-encrypt",
                                local=local)
        assert result.returncode == 0, result.stdout + result.stderr
        assert _mode(local) == 0o755
        assert [_mode(p) for p in local.iterdir()] == [0o600]

    def test_a_partial_file_a_killed_run_left_does_not_lend_its_mode(
            self, tmp_path, gnupg_home):
        """A 0644 straggler under the partial name is replaced, never truncated into."""
        local = tmp_path / "backups"
        local.mkdir()
        artifact = local / "shekel_backup_20260101_000000.sql.gz"
        straggler = local / f".{artifact.name}.partial"
        straggler.write_text("left by a killed run", encoding="utf-8")
        straggler.chmod(0o644)
        bin_dir = _stubs(tmp_path, watch=local)
        result = run_bash(
            'export GNUPGHOME="$3"; umask 022; source "$1"; '
            'DB_CONTAINER=probe-db; PGUSER=probe; PGDATABASE=probe; '
            'write_dump_artifact "$2" "probe advice"',
            str(_LIB), str(artifact), str(gnupg_home), path_prefix=bin_dir,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert [(p.name, _mode(p)) for p in local.iterdir()] == [
            (artifact.name, 0o600)]

    def test_a_nas_partial_a_killed_run_left_does_not_lend_its_mode(
            self, tmp_path):
        """cp keeps an existing file's mode, so the NAS straggler goes first."""
        local, nas = tmp_path / "backups", tmp_path / "nas"
        local.mkdir()
        nas.mkdir()
        name = "shekel_backup_20260101_000000.sql.gz.gpg"
        (local / name).write_bytes(b"probe artifact")
        straggler = nas / f".{name}.partial"
        straggler.write_text("left by a killed run", encoding="utf-8")
        straggler.chmod(0o644)
        result = run_bash(
            'source "$1"; eval "$2"; umask 022; _TMP_FILES=(); '
            'BACKUP_LOCAL_DIR="$3"; BACKUP_NAS_DIR="$4"; BACKUP_FILENAME="$5"; '
            'copy_to_nas',
            str(_LIB), function_text(_BACKUP, "copy_to_nas"), str(local),
            str(nas), name,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert [(p.name, _mode(p)) for p in nas.iterdir()] == [(name, 0o600)]

    def test_the_nas_copy_is_private_whatever_the_umask(self, tmp_path,
                                                        gnupg_home):
        """BAL-663: the copy on the NAS is 0600 too."""
        nas = tmp_path / "nas"
        nas.mkdir()
        result, local = _run_backup(tmp_path, gnupg_home, key=_key(tmp_path),
                                    nas=nas)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "NAS copy complete" in result.stdout, result.stdout
        copies = sorted(nas.iterdir())
        assert [p.name for p in copies] == [p.name for p in local.iterdir()]
        assert [_mode(p) for p in copies] == [0o600]


class TestRestoreSafetyCopyIsEncryptedOrChosen:
    """``restore.sh``: the safety copy is encrypted, explicitly plaintext, or skipped."""

    @staticmethod
    def _assert_nothing_touched(tmp_path: Path, result, dumps: Path) -> None:
        """Exit 1 and no step taken: nothing validated, confirmed, dumped or dropped."""
        assert result.returncode == 1, result.stdout + result.stderr
        assert _taken(tmp_path) == []
        assert not dumps.exists()

    @pytest.mark.parametrize("safety", [(), ("--skip-safety-dump",),
                                        ("--plaintext-safety-dump",)],
                             ids=["encrypted-copy", "no-copy",
                                  "plaintext-copy"])
    def test_an_encrypted_backup_without_a_key_is_refused_first(
            self, tmp_path, gnupg_home, safety):
        """The backup cannot be read, whatever the safety copy: refused, naming the file."""
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz.gpg")
        result, dumps = _run_restore(tmp_path, gnupg_home, "--skip-confirm",
                                     *safety, str(backup))
        self._assert_nothing_touched(tmp_path, result, dumps)
        errors = _errors(result.stdout)
        assert any(str(tmp_path / "absent-key") in line for line in errors)
        assert any(f"The backup {backup} is encrypted" in line
                   for line in errors), errors

    def test_a_plaintext_backup_without_a_key_is_refused_naming_both_flags(
            self, tmp_path, gnupg_home):
        """R-BAL306: the safety copy needs the key, or an explicit choice."""
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz")
        result, dumps = _run_restore(tmp_path, gnupg_home, "--skip-confirm",
                                     str(backup))
        self._assert_nothing_touched(tmp_path, result, dumps)
        errors = _errors(result.stdout)
        assert any(str(tmp_path / "absent-key") in line for line in errors)
        assert any("--plaintext-safety-dump" in line for line in errors)
        assert any("--skip-safety-dump" in line for line in errors)

    def test_both_safety_flags_together_are_refused(self, tmp_path,
                                                    gnupg_home):
        """An unencrypted copy and no copy at all cannot both be meant."""
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz")
        result, dumps = _run_restore(
            tmp_path, gnupg_home, "--skip-confirm", "--plaintext-safety-dump",
            "--skip-safety-dump", str(backup), key=_key(tmp_path),
        )
        self._assert_nothing_touched(tmp_path, result, dumps)
        assert any("--skip-safety-dump" in line
                   and "--plaintext-safety-dump" in line
                   for line in _errors(result.stdout)), result.stdout

    def test_with_a_key_the_safety_copy_is_encrypted_and_private(
            self, tmp_path, gnupg_home):
        """``pre_restore_safety_<ts>.sql.gz.gpg``, 0600 in a 0700 directory."""
        key = _key(tmp_path)
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz")
        result, dumps = _run_restore(tmp_path, gnupg_home, "--skip-confirm",
                                     str(backup), key=key)
        assert result.returncode == 0, result.stdout + result.stderr
        copies = sorted(dumps.iterdir())
        assert len(copies) == 1, copies
        assert copies[0].name.startswith("pre_restore_safety_")
        assert copies[0].name.endswith(".sql.gz.gpg")
        assert _decrypt(copies[0], key, gnupg_home) == _DUMP
        assert (_mode(copies[0]), _mode(dumps)) == (0o600, 0o700)
        assert _taken(tmp_path) == [
            "check_prerequisites", "validate_backup_artifact", "stop_app",
            "drop_and_recreate_database", "restore_backup", "start_app",
            "verify_restore",
        ]

    @pytest.mark.parametrize("with_key", [False, True],
                             ids=["no-key", "key-present"])
    def test_plaintext_safety_dump_is_the_explicit_choice(
            self, tmp_path, gnupg_home, with_key):
        """A ``.sql.gz`` copy, 0600; a WARNING only when a key file was there."""
        key = _key(tmp_path) if with_key else None
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz")
        result, dumps = _run_restore(
            tmp_path, gnupg_home, "--skip-confirm", "--plaintext-safety-dump",
            str(backup), key=key,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        copies = sorted(dumps.iterdir())
        assert [p.name.endswith(".sql.gz") for p in copies] == [True]
        assert gzip.decompress(copies[0].read_bytes()).decode() == _DUMP
        assert _mode(copies[0]) == 0o600
        warnings = [line for line in result.stdout.splitlines()
                    if "--plaintext-safety-dump" in line
                    and "[WARNING]" in line]
        assert len(warnings) == (1 if with_key else 0), result.stdout

    def test_skip_safety_dump_needs_no_key_for_a_plaintext_backup(
            self, tmp_path, gnupg_home):
        """No copy is written, so nothing needs the key."""
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz")
        result, dumps = _run_restore(tmp_path, gnupg_home, "--skip-confirm",
                                     "--skip-safety-dump", str(backup))
        assert result.returncode == 0, result.stdout + result.stderr
        assert not dumps.exists()
        assert "drop_and_recreate_database" in _taken(tmp_path)

    def test_a_safety_dump_that_dies_mid_stream_stops_before_the_drop(
            self, tmp_path, gnupg_home):
        """Nothing written, nothing dropped; the advice names --skip-safety-dump."""
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz")
        result, dumps = _run_restore(tmp_path, gnupg_home, "--skip-confirm",
                                     str(backup), key=_key(tmp_path),
                                     dump_status=1)
        assert result.returncode == 1, result.stdout + result.stderr
        assert list(dumps.iterdir()) == []
        assert _taken(tmp_path) == ["check_prerequisites",
                                    "validate_backup_artifact"]
        errors = _errors(result.stdout)
        assert any("pg_dump exited 1" in line for line in errors), errors
        assert any("--skip-safety-dump" in line for line in errors), errors


    def test_an_interrupted_safety_dump_leaves_no_partial_file(
            self, tmp_path, gnupg_home):
        """A SIGTERM mid-dump ends the run; the EXIT trap removes the partial file.

        The writer removes it only when its pipeline fails, which a signal to
        the shell never reaches.
        """
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz")
        result, dumps = _run_restore(
            tmp_path, gnupg_home, "--skip-confirm", "--plaintext-safety-dump",
            str(backup), interrupt=True,
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert list(dumps.iterdir()) == []
        assert _taken(tmp_path) == ["check_prerequisites",
                                    "validate_backup_artifact"]


class TestVerifyChecksTheKeyOnceBeforeAnyStep:
    """``verify_backup.sh``: an encrypted backup needs the key file, checked up front."""

    def test_an_encrypted_backup_without_a_key_is_refused_first(self,
                                                                tmp_path):
        """No temporary database; the refusal names the key file."""
        backup = _backup_file(tmp_path, "shekel_backup_x.sql.gz.gpg")
        result = _run_verify(tmp_path, str(backup))
        assert result.returncode == 1, result.stdout + result.stderr
        assert _taken(tmp_path) == []
        assert any(str(tmp_path / "absent-key") in line
                   for line in _errors(result.stdout)), result.stdout

    @pytest.mark.parametrize(("name", "with_key"), [
        ("shekel_backup_x.sql.gz.gpg", True),
        ("shekel_backup_x.sql.gz", False),
    ], ids=["encrypted-with-key", "plaintext-without-key"])
    def test_a_readable_backup_is_verified(self, tmp_path, name, with_key):
        """Every step runs when the backup can be read."""
        key = _key(tmp_path) if with_key else None
        result = _run_verify(tmp_path, str(_backup_file(tmp_path, name)),
                             key=key)
        assert result.returncode == 0, result.stdout + result.stderr
        assert _taken(tmp_path) == list(_VERIFY_STEPS)


def _backup_database(tmp_path: Path, script: str | None, mode: int = 0o755):
    """Run ``deploy.sh``'s ``backup_database`` with *script* as ``backup.sh``.

    Args:
        tmp_path: The test's directory, used as the deploy directory.
        script: The stand-in ``backup.sh``'s body, or None for no file.
        mode: The stand-in's mode.
    """
    if script is not None:
        path = tmp_path / "scripts" / "backup.sh"
        path.parent.mkdir()
        path.write_text(f"#!/usr/bin/env bash\n{script}\n", encoding="utf-8")
        path.chmod(mode)
    return run_bash(
        'DEPLOY_DIR="$1"; eval "$2"; eval "$3"; backup_database; '
        'echo returned',
        str(tmp_path), function_text(_DEPLOY, "log"),
        function_text(_DEPLOY, "backup_database"),
    )


class TestTheDeployRefusesWithoutItsBackup:
    """``deploy.sh``'s pre-deploy backup: it runs and succeeds, or the deploy stops."""

    @pytest.mark.parametrize(("script", "mode"), [(None, 0o755),
                                                  ("exit 0", 0o644)],
                             ids=["missing", "not-executable"])
    def test_a_backup_script_it_cannot_run_stops_the_deploy(self, tmp_path,
                                                            script, mode):
        """R-BAL304: no silent skip; the refusal names the script and --skip-backup."""
        result = _backup_database(tmp_path, script, mode)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "returned" not in result.stdout
        errors = _errors(result.stdout)
        assert any(str(tmp_path / "scripts" / "backup.sh") in line
                   for line in errors), errors
        assert any("--skip-backup" in line for line in errors), errors

    def test_a_refusing_backup_stops_the_deploy(self, tmp_path):
        """backup.sh's own refusal (no key, say) ends the deploy before the app."""
        result = _backup_database(tmp_path, "echo refused; exit 1")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "returned" not in result.stdout
        assert any("Aborting deployment" in line
                   for line in _errors(result.stdout)), result.stdout

    def test_a_backup_that_succeeds_lets_the_deploy_continue(self, tmp_path):
        """The one path on: backup.sh ran and exited 0."""
        result = _backup_database(tmp_path, "echo backed-up")
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines()[-1] == "returned"
        assert "backed-up" in result.stdout
