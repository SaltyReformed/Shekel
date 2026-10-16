"""Controls for ``ci.yml``'s install of the pinned linters: one binary per tool, or refuse.

The ``polyglot-lint`` job downloads each linter's release archive, extracts
them all into one directory and finds each tool there by name, so a binary that
moves inside its archive still installs.  It used to install ``$(find ... |
head -1)``: a second file of the same name was installed or passed over by
directory order, silently, and a missing one failed as ``install: cannot stat
''`` naming no tool (plan step balance:X-do, finding BAL-625).  The step now
refuses anything but exactly one match, naming the tool and what it found.

These tests RUN the step's committed text the way GitHub runs a step with no
``shell:`` (``bash -e``), with a stand-in ``curl`` serving planted archives:
nothing is downloaded.
"""
from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from tools.ci import arcs

#: The step under test, by its name in the ``polyglot-lint`` job.
INSTALL_STEP = "Install pinned binary linters"

#: The tools the step finds by name inside an archive, each with the archive
#: compression its download URL names (``.tar.xz`` or ``.tar.gz``).
ARCHIVED_TOOLS = {"shellcheck": "xz", "actionlint": "gz", "typos": "gz", "gitleaks": "gz"}

#: The tools the step downloads as a bare binary (``curl -o``).
BARE_TOOLS = ("shfmt", "biome", "hadolint")

#: A stand-in ``curl``: an archive URL is answered with the planted archive
#: whose name the URL contains; ``-o PATH`` writes a stand-in binary there.
STAND_IN_CURL = """#!/bin/bash
out=""
url=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
if [ -n "${out}" ]; then
    echo "#!/bin/sh" > "${out}"
    exit 0
fi
for tool in shellcheck actionlint typos gitleaks; do
    case "${url}" in
        *"/${tool}/releases/"*) exec cat "${ARCHIVES}/${tool}" ;;
    esac
done
echo "stand-in curl: no archive for ${url}" >&2
exit 22
"""


def _install_run() -> str:
    """Return the install step's ``run`` text from the live workflow."""
    text = (arcs.REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    steps = yaml.safe_load(text)["jobs"]["polyglot-lint"]["steps"]
    return next(step for step in steps if step.get("name") == INSTALL_STEP)["run"]


def _archive(copies: dict[str, int], compression: str) -> bytes:
    """Build a tar archive holding ``copies[name]`` files of each name, one per directory."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=f"w:{compression}") as tar:
        for name, count in copies.items():
            for index in range(count):
                body = f"#!/bin/sh\necho {name} {index}\n".encode()
                member = tarfile.TarInfo(f"release-{index}/{name}")
                member.size = len(body)
                member.mode = 0o755
                tar.addfile(member, io.BytesIO(body))
    return buffer.getvalue()


def _run(tmp_path: Path, copies: dict[str, int]) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the install step with each archived tool planted ``copies[tool]`` times.

    Returns:
        The finished process and the directory the step installs into.
    """
    archives = tmp_path / "archives"
    stand_ins = tmp_path / "stand-ins"
    home = tmp_path / "home"
    for directory in (archives, stand_ins, home, tmp_path / "tmp"):
        directory.mkdir()
    for tool, compression in ARCHIVED_TOOLS.items():
        (archives / tool).write_bytes(_archive({tool: copies[tool]}, compression))
    curl = stand_ins / "curl"
    curl.write_text(STAND_IN_CURL)
    curl.chmod(0o755)
    result = subprocess.run(
        ["bash", "-e", "-c", _install_run()],
        env={
            "PATH": f"{stand_ins}:/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(tmp_path / "tmp"),
            "GITHUB_PATH": str(tmp_path / "github_path"),
            "ARCHIVES": str(archives),
        },
        capture_output=True, text=True, check=False,
    )
    return result, home / ".local" / "bin"


class TestEachToolInstallsFromExactlyOneMatch:
    """The step installs every tool once, or refuses naming the tool it could not place."""

    def test_one_match_each_installs_every_tool(self, tmp_path):
        """As released, one binary per archive: every tool lands in ~/.local/bin."""
        result, installed = _run(tmp_path, dict.fromkeys(ARCHIVED_TOOLS, 1))
        assert result.returncode == 0, result.stderr
        assert sorted(p.name for p in installed.iterdir()) == sorted(
            [*ARCHIVED_TOOLS, *BARE_TOOLS],
        )
        # The installed file IS the planted one, not merely a file of its name.
        assert (installed / "typos").read_text() == "#!/bin/sh\necho typos 0\n"

    @pytest.mark.parametrize("count", [0, 2])
    def test_anything_but_one_match_refuses_naming_the_tool(self, tmp_path, count):
        """Two files named ``typos``, or none: the step fails and says which tool, and how many."""
        copies = dict.fromkeys(ARCHIVED_TOOLS, 1)
        copies["typos"] = count
        result, installed = _run(tmp_path, copies)
        assert result.returncode == 1, (result.stdout, result.stderr)
        assert f"expected exactly one 'typos' in the downloaded archives, found {count}" in (
            result.stderr
        ), result.stderr
        # The loop stops AT the tool it cannot place: the tools after it in
        # the loop's order (gitleaks) and the bare downloads are not installed.
        assert not (installed / "typos").exists()
        assert not (installed / "gitleaks").exists()
        assert not (installed / "shfmt").exists()
