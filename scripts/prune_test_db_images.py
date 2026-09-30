"""Remove the test-db images earlier bakes left behind, keeping every live one.

Every bake by :mod:`scripts.build_test_db_image` commits a new
``shekel-test-db:<key>`` image, one per content key, and nothing in the
harness removed an old one.  Measured 2026-09-29 at 20:31 on this host's
rootless daemon: 90 such images, 87 of them baked that same day.  The
developer ruled on it that day, verbatim: "After each new build, the runner
removes old test images except every live worktree's current one and anything
under 24 hours old. It uses plain removal, so an image a running suite still
uses is never touched."  He accepted it with this condition, also verbatim:
"If it can't work out any worktree's current image, it deletes nothing."

:func:`prune_stale_images` is that rule, and the builder calls it only after a
build actually ran.  Three decisions carry it:

1. THE KEEP SET IS EVERY LIVE WORKTREE'S CURRENT TAG, AND EACH WORKTREE
   COMPUTES ITS OWN.  Several worktrees run suites at once, each on its own
   key.  CURRENT means what the worktree's files hash to now, uncommitted
   edits included: the image its next suite would use.  The key function is
   code on a branch, so computing another tree's key with THIS tree's code
   would name the wrong image the day the two differ; each worktree's own
   builder is asked instead (``--print-tag``, run in that worktree).  A
   worktree git marks ``prunable`` has lost its directory and has no current
   image, so it is skipped.  Any other worktree whose tag cannot be obtained
   stops the prune before anything is removed.  That includes a LOCKED
   worktree whose directory was deleted: git never marks a locked worktree
   prunable (``git-worktree(1)``, ``lock``), so it stops every prune, naming
   its path, until ``git worktree unlock`` and ``git worktree prune`` clear it.

2. ONLY IMAGES DOCKER ITSELF CALLS OLD ARE CANDIDATES.  ``--filter
   until=24h`` compares each image's creation time on the daemon, so no clock
   or timestamp is parsed here.

3. REMOVAL IS A PLAIN ``docker rmi``, ONE TAG AT A TIME, NEVER ``-f``.
   Docker's own manual (``docker-image-rm(1)``): "You cannot remove an image of
   a running container unless you use the -f option."  That refusal is what
   leaves a running suite's image in place, so a refused tag is counted and
   passed over, never forced.  It is docker's DOCUMENTED contract; it was not
   measured on this host's rootless daemon, which uses the containerd image
   store.  One call per tag lets the exit status alone say which tags went and
   which were refused.

A prune problem never fails the build or the test run: whatever goes wrong,
a hang included, is reported in one line and the caller carries on.

One window stays open.  ``scripts/test.sh`` resolves its tag before it starts
its container, so a worktree whose inputs change in between can have its
previous image, if over 24 hours old, removed by another worktree's prune in
that window; its ``docker run`` then fails loudly and a re-run rebuilds.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# "Anything under 24 hours old" is kept (the ruling), so only images docker
# reports as created before this long ago are candidates.
_MINIMUM_AGE = "24h"

# Where the builder sits in every worktree.  Each worktree's own copy is the
# one that knows that worktree's key.
_BUILDER = Path("scripts/build_test_db_image.py")

# Docker's grammar for a tag.  A ``--print-tag`` line, and a candidate from
# the listing, must be ``<repo>:<tag>`` in it; ``<none>`` is not.
_TAG = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"

# A HANG GUARD, not a performance bound: every command here finishes in well
# under a second on this host (all nine worktrees' ``--print-tag`` took 0.64 s
# together, measured 2026-09-29), and a wedged daemon would otherwise hold the
# build, and the test run behind it, forever.
_COMMAND_TIMEOUT_SECONDS = 60


class PruneRefused(RuntimeError):
    """The keep set or the candidates could not be established.

    Raised only BEFORE the first removal, so catching it means nothing was
    removed.
    """


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its output; the caller reads the status.

    Undecodable output is REPLACED rather than raised on, where a decode
    error would have escaped every handler here and failed the build.  A
    mangled worktree path or printed tag then fails its own check and stops
    the prune; a mangled listing line matches no tag and is never a
    candidate.

    Args:
        command: argv to execute.
        cwd: Directory to run it in; this process's own when omitted.

    Returns:
        The completed process.

    Raises:
        OSError: When the command cannot be started at all, for example a
            missing binary or a ``cwd`` that no longer exists.
        subprocess.TimeoutExpired: When it runs past
            :data:`_COMMAND_TIMEOUT_SECONDS`; it is killed first.
    """
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, errors="replace",
        timeout=_COMMAND_TIMEOUT_SECONDS, check=False,
    )


def _last_line(text: str) -> str:
    """Return the last non-blank line of a command's output, or ``""``.

    A traceback's last line names the exception, and the report this feeds
    must stay on one line.

    Args:
        text: Captured output.

    Returns:
        That line, stripped.
    """
    lines = text.strip().splitlines()
    return lines[-1].strip() if lines else ""


def _is_tag(image_repo: str, reference: str) -> bool:
    """Return whether ``reference`` is exactly ``<image_repo>:<tag>``.

    Args:
        image_repo: The repository every test-db image is tagged under.
        reference: The string to check.

    Returns:
        True for a well-formed reference in that repository.
    """
    return re.fullmatch(rf"{re.escape(image_repo)}:{_TAG}", reference) is not None


def live_worktrees(repo_root: Path) -> list[Path]:
    """Return every worktree of this repository that git does not mark prunable.

    Args:
        repo_root: Any worktree of the repository.

    Returns:
        The worktrees' directories, in git's order.

    Raises:
        PruneRefused: When git cannot list the worktrees, or lists a record
            that does not start with its ``worktree`` line.
        OSError: When git cannot be started at all.
        subprocess.TimeoutExpired: When git hangs.
    """
    listing = _run(["git", "-C", str(repo_root), "worktree", "list", "--porcelain"])
    if listing.returncode != 0:
        raise PruneRefused(
            f"git worktree list exited {listing.returncode}: "
            f"{_last_line(listing.stderr)}"
        )
    worktrees: list[Path] = []
    # One record per worktree, each ended by an empty line; its first line is
    # always ``worktree <path>`` (git-worktree(1), "Porcelain Format").
    for record in listing.stdout.strip().split("\n\n"):
        lines = record.splitlines()
        if not lines or not lines[0].startswith("worktree "):
            raise PruneRefused(f"git worktree list printed {record!r}")
        if any(line == "prunable" or line.startswith("prunable ") for line in lines):
            continue
        worktrees.append(Path(lines[0].removeprefix("worktree ")))
    return worktrees


def live_tags(image_repo: str, repo_root: Path) -> set[str]:
    """Return the current image tag of every live worktree, each by its own builder.

    Args:
        image_repo: The repository every test-db image is tagged under.
        repo_root: Any worktree of the repository.

    Returns:
        The ``<image_repo>:<key>`` references to keep.

    Raises:
        PruneRefused: When the worktrees cannot be listed, or ANY live
            worktree's tag cannot be obtained -- a keep set missing one
            worktree would remove the image that worktree is about to run.
        OSError: When git or a worktree's builder cannot be started at all,
            for example because the worktree's directory is gone.
        subprocess.TimeoutExpired: When git or a builder hangs.
    """
    tags: set[str] = set()
    for worktree in live_worktrees(repo_root):
        printed = _run(
            [sys.executable, str(worktree / _BUILDER), "--print-tag"], cwd=worktree
        )
        tag = printed.stdout.strip()
        if printed.returncode != 0 or not _is_tag(image_repo, tag):
            raise PruneRefused(
                f"could not work out the current image of {worktree}: "
                f"--print-tag exited {printed.returncode}, printed {tag!r}, "
                f"said {_last_line(printed.stderr)!r}"
            )
        tags.add(tag)
    return tags


def stale_candidates(image_repo: str) -> list[str]:
    """Return every tag in ``image_repo`` that docker says is over 24 hours old.

    Args:
        image_repo: The repository every test-db image is tagged under.

    Returns:
        ``<image_repo>:<tag>`` references, untagged (``<none>``) ones left out.

    Raises:
        PruneRefused: When docker cannot list the images.
        OSError: When docker cannot be started at all.
        subprocess.TimeoutExpired: When the listing hangs.
    """
    listing = _run(
        [
            "docker", "image", "ls",
            "--filter", f"reference={image_repo}",
            "--filter", f"until={_MINIMUM_AGE}",
            "--format", "{{.Repository}}:{{.Tag}}",
        ]
    )
    if listing.returncode != 0:
        raise PruneRefused(
            f"docker image ls exited {listing.returncode}: "
            f"{_last_line(listing.stderr)}"
        )
    return [line for line in listing.stdout.split() if _is_tag(image_repo, line)]


def prune_stale_images(image_repo: str, repo_root: Path) -> None:
    """Remove every stale test-db image that no live worktree still names.

    Prints one line: what was removed, kept and refused, or why nothing was.
    Raises nothing it can meet, because a prune problem must never fail the
    build that called it or the test run behind that.

    Args:
        image_repo: The repository every test-db image is tagged under.
        repo_root: Any worktree of the repository.
    """
    removed = 0
    refused = 0
    try:
        keep = live_tags(image_repo, repo_root)
        candidates = stale_candidates(image_repo)
        doomed = [tag for tag in candidates if tag not in keep]
        for tag in doomed:
            if _run(["docker", "rmi", tag]).returncode == 0:
                removed += 1
            else:
                refused += 1
    except PruneRefused as exc:
        print(f"  test-db image prune removed nothing: {exc}", file=sys.stderr)
        return
    except (OSError, subprocess.SubprocessError) as exc:
        print(
            f"  test-db image prune stopped after removing {removed}: {exc}",
            file=sys.stderr,
        )
        return
    print(
        f"  test-db images over {_MINIMUM_AGE} old: removed {removed}, "
        f"kept {len(candidates) - len(doomed)} (a live worktree's current "
        f"image), refused {refused} (docker rmi failed: in use, or already gone)"
    )
