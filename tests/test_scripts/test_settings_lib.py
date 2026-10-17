"""A shell script's settings come from ONE table, and its ``--help`` prints them.

Plan step **balance:X-do** (leaf L2b-2a; the coordinator's ruling on Q7(iii),
2026-10-17; finding BAL-660's fold): ``scripts/_settings_lib.sh`` holds the
generic reader (``read_settings``) and printer (``print_settings``), and
``scripts/_backup_lib.sh`` fills the table ``SHEKEL_SETTING_DEFAULTS`` with the
backup family's defaults.  A script names the settings it reads once, and that
one call both sets them and is what its ``--help`` prints.  Graded here:

* each backup script's ``--help`` lists exactly the table's settings the
  script reads, each with the table's default, and runs no ``docker``;
* the defaults come from the table: a copy whose table holds probes prints
  only the probes, resolves only the probes at the top level (where the
  scripts read their settings; ``main`` is not run), and shows in its
  ``--help`` no real default of a setting it reads (numbers included) nor
  any path or name default at all; an environment value wins over the
  default without changing what ``--help`` calls the default;
* a per-run default prints its placeholder, never one run's process id;
* no script spells a default in a ``${NAME:-...}`` form, and no CODE line of
  one spells a default distinctive enough to search for (a path or a name;
  a bare number or ``shekel`` is not, and a comment is prose, which the
  runbooks' leaf owns);
* the reader refuses a setting the table lacks, and needs no family's lib.

The scripts that drop or write databases (``restore.sh``,
``verify_backup.sh``) are run whole only with ``--help``, which exits before
any step; the resolution checks run on copies whose last line, ``main "$@"``,
is replaced by a print of the settings.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_scripts._shell_harness import fake_docker, run_bash

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_SETTINGS_LIB = _SCRIPTS_DIR / "_settings_lib.sh"
_BACKUP_LIB = _SCRIPTS_DIR / "_backup_lib.sh"

#: The scripts whose settings come from the backup family's table.
_BACKUP_SCRIPTS = ("backup.sh", "restore.sh", "verify_backup.sh",
                   "backup_retention.sh")

#: The libs a copied script needs beside it.
_LIBS = ("_settings_lib.sh", "_backup_lib.sh", "_docker_lib.sh")

#: One ``--help`` entry as print_settings writes it: the name and its meaning
#: on one line, its default on the next.
_ENTRY = re.compile(r"^    ([A-Z][A-Z0-9_]*) +(.+)\n +default: (.*)$", re.M)

#: The heading print_settings writes above the entries.
_HEADING = "Environment variables (each read from the environment, else its default):"


def _table(lib: Path = _BACKUP_LIB) -> dict[str, str]:
    """Return the table *lib* builds, as bash holds it after sourcing it.

    An empty table is refused here: bash expands the keys of an array that
    does not exist to nothing, even under ``set -u``, and every check that
    searches for the table's values would then search for none.
    """
    result = run_bash(
        'source "$1"\n'
        'for name in "${!SHEKEL_SETTING_DEFAULTS[@]}"; do\n'
        '    printf "%s\\t%s\\n" "${name}" "${SHEKEL_SETTING_DEFAULTS[${name}]}"\n'
        'done',
        str(lib),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout, f"{lib} builds no settings table"
    return dict(line.split("\t", 1) for line in result.stdout.splitlines())


def _placeholder() -> str:
    """Return what a per-run default holds where the run's process id goes."""
    result = run_bash('source "$1"; printf "%s" "${SHEKEL_SETTING_PID_PLACEHOLDER}"',
                      str(_SETTINGS_LIB))
    assert result.returncode == 0 and result.stdout, result.stderr
    return result.stdout


def _code(script: Path) -> str:
    """Return *script*'s lines that are not comments."""
    return "\n".join(line for line in script.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


def _bounded(value: str) -> str:
    """Return a pattern finding *value* as a whole token, not inside a path or name.

    A ``-`` may come BEFORE it, so ``${X:-value}`` is found too.
    """
    return rf"(?<![\w/.]){re.escape(value)}(?![\w/.-])"


def _expanded(script: Path, table: dict[str, str]) -> set[str]:
    """Return the table's settings that *script*'s own code expands.

    The derivation reads ``$NAME`` and ``${NAME...}`` on every line that is
    not a comment, and errs both ways.  A setting only a lib function reads
    is missing from it, a loud false failure.  A ``$NAME`` in a heredoc or a
    string counts as a read, so a declared setting named only there passes;
    and a lib function reading a setting the script never declared is not
    seen at all: it fails only at run time, and only on that path.
    """
    names = re.findall(r"\$\{?([A-Z][A-Z0-9_]*)", _code(script))
    return set(names) & set(table)


def _help(script: Path, tmp_path: Path,
          env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess, Path]:
    """Run *script* ``--help`` whole, with a ``docker`` that fails and logs.

    Returns:
        The completed process and the stub's invocation log.
    """
    log = fake_docker(tmp_path / "bin", stdout="", status=1)
    result = subprocess.run(
        ["bash", str(script), "--help"], capture_output=True, text=True,
        timeout=30, check=False,
        env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", **(env or {})},
    )
    return result, log


def _entries(help_text: str) -> list[tuple[str, str, str]]:
    """Return the (name, meaning, default) entries under the settings heading."""
    assert _HEADING in help_text, help_text
    return _ENTRY.findall(help_text.split(_HEADING, 1)[1])


def _probed_copy(tmp_path: Path, script: str) -> Path:
    """Copy *script* and the libs, every table default replaced by a probe.

    Each default becomes ``probe-<NAME>-<placeholder>``, so the copy also
    shows the placeholder resolved per run in every setting.

    Returns:
        The copied script.
    """
    folder = tmp_path / "scripts"
    folder.mkdir()
    for name in (script, *_LIBS):
        shutil.copy2(_SCRIPTS_DIR / name, folder / name)
    with (folder / "_backup_lib.sh").open("a", encoding="utf-8") as lib:
        lib.write(
            '\nfor _probe in "${!SHEKEL_SETTING_DEFAULTS[@]}"; do\n'
            '    SHEKEL_SETTING_DEFAULTS[${_probe}]='
            '"probe-${_probe}-${SHEKEL_SETTING_PID_PLACEHOLDER}"\n'
            'done\n'
        )
    return folder / script


def _resolved(copy: Path, names: set[str],
              env: dict[str, str] | None = None) -> dict[str, str]:
    """Run *copy* with its last line replaced by a print of *names* and ``$$``.

    A ``docker`` that fails and logs is first on ``PATH``, and must not be
    called: the top level of a script touches no container.
    """
    text = copy.read_text(encoding="utf-8")
    assert text.endswith('\nmain "$@"\n'), "the script's last line moved"
    prints = "".join(f'printf "%s\\n" "{name}=${{{name}}}"\n'
                     for name in sorted(names))
    copy.write_text(text[:-len('main "$@"\n')] + prints + 'printf "pid=%s\\n" "$$"\n',
                    encoding="utf-8")
    bin_dir = copy.parents[1] / "resolve-bin"
    log = fake_docker(bin_dir, stdout="", status=1)
    result = subprocess.run(
        ["bash", str(copy)], capture_output=True, text=True, timeout=30,
        check=False, env={"PATH": f"{bin_dir}:/usr/bin:/bin", **(env or {})},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not log.exists(), log.read_text(encoding="utf-8")
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


class TestHelpListsWhatTheScriptReads:
    """``--help`` names every setting the script reads, with the table's default."""

    @pytest.mark.parametrize("script", _BACKUP_SCRIPTS)
    def test_help_lists_exactly_the_settings_the_script_reads(self, tmp_path, script):
        """The listed names are the script's reads; each default is the table's.

        The reads are DERIVED from the script's own text (``_expanded``
        states which way that derivation errs).  ``--help`` runs whole in an
        empty environment and touches no container.
        """
        table = _table()
        result, log = _help(_SCRIPTS_DIR / script, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        entries = _entries(result.stdout)
        names = [name for name, _, _ in entries]
        assert len(names) == len(set(names)), names
        assert set(names) == _expanded(_SCRIPTS_DIR / script, table)
        assert {name: default for name, _, default in entries} == {
            name: table[name] for name in names
        }
        assert all(meaning.strip() for _, meaning, _ in entries), entries
        assert not log.exists(), log.read_text(encoding="utf-8")

    def test_a_per_run_default_prints_its_placeholder(self, tmp_path):
        """VERIFY_DB's default is shown with the placeholder, never a run's pid.

        The script is exec'd from a shell that first prints its own pid, so
        the pid printed IS the script's ``$$``: the name that run resolves
        appears nowhere in its ``--help``.  The same table entry, read,
        resolves to the reading process's id in the placeholder's place.
        """
        table, placeholder = _table(), _placeholder()
        assert placeholder in table["VERIFY_DB"], table["VERIFY_DB"]
        fake_docker(tmp_path / "bin", stdout="", status=1)
        result = subprocess.run(
            ["bash", "-c", 'printf "%s\\n" "$$"; exec bash "$1" --help', "_",
             str(_SCRIPTS_DIR / "verify_backup.sh")],
            capture_output=True, text=True, timeout=30, check=False,
            env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        pid, help_text = result.stdout.split("\n", 1)
        shown = {name: default for name, _, default in _entries(help_text)}
        assert shown["VERIFY_DB"] == table["VERIFY_DB"]
        assert table["VERIFY_DB"].replace(placeholder, pid) not in help_text
        resolved = run_bash(
            'source "$1"; read_settings VERIFY_DB probe; '
            'printf "%s %s" "${VERIFY_DB}" "$$"',
            str(_BACKUP_LIB), path_prefix=tmp_path / "bin",
        )
        assert resolved.returncode == 0, resolved.stderr
        value, pid = resolved.stdout.split(" ")
        assert value == table["VERIFY_DB"].replace(placeholder, pid)


class TestEveryDefaultComesFromTheTable:
    """A copy with probe defaults prints and resolves the probes, and nothing else."""

    @pytest.mark.parametrize("script", _BACKUP_SCRIPTS)
    def test_help_prints_the_table_s_defaults_and_no_other(self, tmp_path, script):
        """Every printed default is the probe; no real default appears at all.

        A default spelled anywhere else in the script (an Options line, a
        meaning, the heredoc) would still print the real value, which the
        second assertion searches for as a whole token: the default of every
        setting the script reads, numbers included, and every path or name
        default in the table, whoever reads it.
        """
        real = _table()
        copy = _probed_copy(tmp_path, script)
        result, _ = _help(copy, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        placeholder = _placeholder()
        entries = _entries(result.stdout)
        assert entries, result.stdout
        assert {name: default for name, _, default in entries} == {
            name: f"probe-{name}-{placeholder}" for name, _, _ in entries
        }
        searched = {real[name] for name in _expanded(_SCRIPTS_DIR / script, real)}
        searched |= {value for value in real.values() if re.search(r"[/_-]", value)}
        leaked = [value for value in sorted(searched)
                  if re.search(_bounded(value), result.stdout)]
        assert leaked == [], result.stdout

    @pytest.mark.parametrize("script", _BACKUP_SCRIPTS)
    def test_an_empty_environment_resolves_to_the_table(self, tmp_path, script):
        """Each setting the script reads holds its probe, the pid in place."""
        names = _expanded(_SCRIPTS_DIR / script, _table())
        resolved = _resolved(_probed_copy(tmp_path, script), names)
        assert {name: resolved[name] for name in names} == {
            name: f"probe-{name}-{resolved['pid']}" for name in names
        }

    @pytest.mark.parametrize("script", _BACKUP_SCRIPTS)
    def test_an_environment_value_wins_and_help_still_shows_the_default(
            self, tmp_path, script):
        """The environment sets the value; ``--help`` keeps printing the default."""
        names = _expanded(_SCRIPTS_DIR / script, _table())
        env = {name: f"env-{name}" for name in names}
        copy = _probed_copy(tmp_path, script)
        result, _ = _help(copy, tmp_path, env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        placeholder = _placeholder()
        assert {name: default for name, _, default in _entries(result.stdout)} == {
            name: f"probe-{name}-{placeholder}" for name in names
        }
        resolved = _resolved(copy, names, env=env)
        assert {name: resolved[name] for name in names} == env


class TestNoScriptSpellsADefault:
    """Every default goes through the table."""

    @pytest.mark.parametrize("script", (*_BACKUP_SCRIPTS, "_backup_lib.sh"))
    def test_no_default_expansion_is_left(self, script):
        """No ``${NAME:-...}``, ``${NAME-...}`` or ``${NAME:=...}`` of a setting."""
        text = (_SCRIPTS_DIR / script).read_text(encoding="utf-8")
        assert re.findall(r"\$\{[A-Z][A-Z0-9_]*:?[-=][^}]*\}", text) == []

    @pytest.mark.parametrize("script", _BACKUP_SCRIPTS)
    def test_no_code_line_spells_a_path_or_name_default(self, script):
        """No default holding ``/``, ``_`` or ``-`` is written into the code.

        A bare number or ``shekel`` is too common to search for, and comments
        are not read (the runbooks' leaf owns the prose); ``--help`` and the
        resolution checks above cover what this cannot.
        """
        code = _code(_SCRIPTS_DIR / script)
        found = [value for value in sorted(_table().values())
                 if re.search(r"[/_-]", value) and re.search(_bounded(value), code)]
        assert found == []


class TestTheReader:
    """``read_settings`` alone: what it refuses, and what it needs."""

    def test_a_setting_with_no_default_is_refused(self):
        """A name the table lacks ends the script, naming it, even inside ``|| true``."""
        result = run_bash(
            'source "$1"; read_settings PGUSER "a user" NO_SUCH_SETTING "probe" '
            '|| true; echo reached',
            str(_BACKUP_LIB),
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "reached" not in result.stdout
        assert "NO_SUCH_SETTING has no default" in result.stderr, result.stderr

    def test_a_setting_without_a_meaning_is_refused(self):
        """An odd argument count (a name without a meaning) ends the script.

        Even inside ``|| true``: the reader exits, it does not return.
        """
        result = run_bash('source "$1"; read_settings PGUSER || true; echo reached',
                          str(_BACKUP_LIB))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "reached" not in result.stdout
        assert "NAME MEANING pairs" in result.stderr, result.stderr

    def test_an_empty_value_takes_the_default(self):
        """An exported but empty setting is unset, as ``${NAME:-default}`` read it."""
        result = run_bash(
            'source "$1"; SHEKEL_SETTING_DEFAULTS[PROBE_SETTING]=probe-default\n'
            'export PROBE_SETTING=""; read_settings PROBE_SETTING "a probe"\n'
            'printf "%s" "${PROBE_SETTING}"',
            str(_SETTINGS_LIB),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == "probe-default"

    def test_the_reader_and_printer_need_no_family_lib(self, tmp_path):
        """The generic lib works alone: the deploy family can source it.

        It is copied into an otherwise empty directory, given a table of its
        caller's own, and used: no family's lib is there to be loaded.
        """
        alone = tmp_path / "_settings_lib.sh"
        shutil.copy2(_SETTINGS_LIB, alone)
        result = run_bash(
            'source "$1"; SHEKEL_SETTING_DEFAULTS[PROBE_SETTING]=probe-default\n'
            'read_settings PROBE_SETTING "a probe"; printf "%s\\n" "${PROBE_SETTING}"\n'
            'print_settings',
            str(alone),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines()[0] == "probe-default"
        assert _entries(result.stdout) == [
            ("PROBE_SETTING", "a probe", "probe-default"),
        ]
