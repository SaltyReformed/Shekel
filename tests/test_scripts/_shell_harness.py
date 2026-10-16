"""The shell harness the operations scripts' tests share.

Three helpers, moved here unchanged from
``tests/test_scripts/test_ops_shell_readers.py`` (plan step balance:X-do)
when a second module came to need them, so the extraction rule, the
``docker`` stub and the strict-mode runner each have one spelling:

* :func:`function_text` reads ONE shell function's definition out of a
  script, so a test can run that function without running the script (the
  scripts graded this way write production paths or drop databases);
* :func:`fake_docker` installs a ``docker`` stub that prints a fixed answer,
  exits a fixed status and logs every invocation's arguments;
* :func:`run_bash` runs a program under the scripts' own
  ``set -euo pipefail``.
"""
from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path


def function_text(script: Path, name: str) -> str:
    """Return shell function *name*'s definition from *script*, verbatim.

    A definition opens with ``name() {`` at the start of a line and closes
    with the first ``}`` alone at the start of a line, which is how every
    top-level function in these scripts is written (shfmt enforces it).

    Args:
        script: The script holding the function.
        name: The function's name.

    Returns:
        The definition, from its first line through its closing brace.
    """
    found = re.findall(
        rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
        script.read_text(encoding="utf-8"), flags=re.S | re.M,
    )
    assert len(found) == 1, (
        f"expected one definition of {name}() in {script}, found {len(found)}"
    )
    return found[0]


def fake_docker(bin_dir: Path, *, stdout: str, status: int) -> Path:
    """Install a ``docker`` stub that prints *stdout* and exits *status*.

    Every invocation's arguments are appended to ``docker.log`` beside it.

    Args:
        bin_dir: Directory placed first on ``PATH``.
        stdout: What the stub prints, verbatim.
        status: The stub's exit status.

    Returns:
        The invocation log's path.
    """
    bin_dir.mkdir(exist_ok=True)
    log = bin_dir / "docker.log"
    answer = bin_dir / "docker.out"
    answer.write_text(stdout, encoding="utf-8")
    stub = bin_dir / "docker"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >>'{log}'
        cat '{answer}'
        exit {status}
        """), encoding="utf-8")
    stub.chmod(0o755)
    return log


def run_bash(program: str, *args: str, path_prefix: Path | None = None,
          ) -> subprocess.CompletedProcess:
    """Run *program* under the swept scripts' own ``set -euo pipefail``.

    A function its script calls inside a command substitution is called the
    same way here, since bash runs that subshell WITHOUT errexit: grading it
    bare under ``set -e`` would grade a context its callers never give it.

    Args:
        program: Bash source, reading its inputs from ``$1``, ``$2`` ...
        args: The positional parameters.
        path_prefix: A directory to put first on ``PATH`` (the stubs).

    Returns:
        The completed process, stdout and stderr captured.
    """
    env = None
    if path_prefix is not None:
        env = {"PATH": f"{path_prefix}:/usr/bin:/bin"}
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{program}", "_", *args],
        capture_output=True, text=True, timeout=30, check=False, env=env,
    )
