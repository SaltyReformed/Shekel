"""After a release: every worktree, every branch already in ``main``, and every database on
the dev server, each marked MADE when this release made it (read-only).

The skill removes only what is marked MADE: the release's own ``release/<date>`` branch,
local and on ``origin``, and its rehearsal kit's worktrees (inside the directory the kit
makes them in) and database clones (by the prefix the kit names them with).  Everything
else is reported with the worktree it is checked out in, for its owner: a peer lane's
worktree is not this session's to remove.  ``main`` and ``dev`` are never listed, so no
release can be told to delete them.

The dev database server's container, user and database are read from the dev compose
file, their one home; the database listing runs behind an injected runner, so a test
never calls docker.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from tools.ci.gitcmd import git
from tools.release._carried import ReleaseError

#: The dev compose file and the service whose server holds the dev databases.
COMPOSE, DB_SERVICE = "docker-compose.dev.yml", "db"
#: The long-lived branches, which no census lists.
LONG_LIVED = frozenset({"refs/heads/main", "refs/heads/dev", "refs/remotes/origin/main",
                        "refs/remotes/origin/dev", "refs/remotes/origin/HEAD"})
#: The prefix a release branch's name must carry, so no other branch is marked MADE.
RELEASE_PREFIX = "release/"


@dataclass(frozen=True)
class Worktree:
    """One worktree: its path, the branch it has checked out (None when detached) and
    its HEAD."""

    path: str
    branch: str | None
    head: str


@dataclass(frozen=True)
class Marked:
    """One thing the census lists, whether this release made it, and what else locates it:
    a worktree's branch (or its detached HEAD), a branch's worktree (None when it is
    checked out nowhere), nothing for a database."""

    name: str
    made: bool
    where: str | None = None


@dataclass(frozen=True)
class Census:
    """The worktrees, the branches whose tip ``main`` holds, and the dev databases."""

    main: str
    worktrees: tuple[Marked, ...]
    branches: tuple[Marked, ...]
    databases: tuple[Marked, ...]


@dataclass(frozen=True)
class Made:
    """What this release made: its branch (``release/<date>``), the directory its
    rehearsal kit makes its worktrees in, and the prefix its database clones' names
    start with; None for none."""

    release_branch: str | None = None
    worktree_dir: str | None = None
    database_prefix: str | None = None


def worktrees(root: Path) -> list[Worktree]:
    """Every worktree of the repository at ``root``, as ``git worktree list`` gives it."""
    found, fields = [], {}
    for line in [*git(root, "worktree", "list", "--porcelain").splitlines(), ""]:
        if line:
            key, _, value = line.partition(" ")
            fields[key] = value
        elif fields:
            branch = fields.get("branch")
            found.append(Worktree(fields["worktree"], branch, fields.get("HEAD", "")))
            fields = {}
    return found


def merged_branches(root: Path, main: str) -> list[str]:
    """Every local and remote-tracking branch whose tip ``main`` holds, but the long-lived."""
    out = git(root, "for-each-ref", f"--merged={main}", "--format=%(refname)",
              "refs/heads", "refs/remotes")
    return [ref for ref in out.splitlines() if ref and ref not in LONG_LIVED]


@dataclass(frozen=True)
class Server:
    """The dev database server, as the compose file names it: its container, user and
    database."""

    container: str
    user: str
    database: str

    @property
    def listing(self) -> list[str]:
        """``docker exec`` + ``psql`` argv listing its databases, read-only."""
        return ["docker", "exec", self.container, "psql", "-U", self.user, "-d",
                self.database, "-Atc",
                "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname"]


def dev_server(root: Path) -> Server:
    """The dev database server, from the compose file, its one home.

    Raises:
        ReleaseError: when the compose file names no such service, container or variable.
        OSError: when the compose file cannot be read.
        yaml.YAMLError: when it is not YAML.
    """
    composed = yaml.safe_load((root / COMPOSE).read_text(encoding="utf-8"))
    try:
        service = composed["services"][DB_SERVICE]
        env = service["environment"]
        return Server(service["container_name"], env["POSTGRES_USER"], env["POSTGRES_DB"])
    except (KeyError, TypeError) as missing:
        raise ReleaseError(f"{COMPOSE} gives service {DB_SERVICE!r} no container_name, "
                           f"POSTGRES_USER or POSTGRES_DB ({missing!r})") from missing


def docker_listing(argv: list[str]) -> list[str]:
    """Run ``argv`` and return its output's lines.

    Raises:
        subprocess.CalledProcessError: when it exits non-zero.
    """
    done = subprocess.run(argv, capture_output=True, text=True, check=True)
    return [line for line in done.stdout.splitlines() if line]


def _inside(path: str, directory: str | None) -> bool:
    """Whether ``path`` lies strictly inside ``directory`` (None: nothing does), compared
    as resolved paths, so a sibling whose name merely starts the same is outside."""
    if directory is None:
        return False
    there = Path(directory).resolve()
    return Path(path).resolve() != there and Path(path).resolve().is_relative_to(there)


def _too_broad(made: Made, trees: list[Worktree], server: Server) -> list[str]:
    """Why ``made`` would mark what no release makes, which the skill would then delete:
    an empty prefix or directory; a database prefix the dev database or the server's own
    ``postgres`` starts with; a kit directory holding the repository's main worktree or
    any worktree with a branch checked out (a kit's own are detached).  What it cannot
    tell: a DETACHED peer worktree inside the kit's directory, and a peer's clone whose
    name starts with the kit's prefix, both of which it marks MADE; the kit's directory
    and prefix must be its own (e.g. ``shekel_rel_<date>_``)."""
    reasons = []
    if made.database_prefix is not None:
        reasons += [f"--kit-databases {made.database_prefix!r} would mark {name!r}"
                    for name in (server.database, "postgres")
                    if name.startswith(made.database_prefix)]
    if made.worktree_dir is not None:
        if not made.worktree_dir.strip():
            reasons.append("--kit-worktrees is empty")
        else:
            reasons += [f"--kit-worktrees {made.worktree_dir!r} holds {tree.path}, "
                        + ("the repository's main worktree" if index == 0 else
                           f"which has {tree.branch} checked out")
                        for index, tree in enumerate(trees)
                        if (index == 0 or tree.branch)
                        and (_inside(tree.path, made.worktree_dir)
                             or Path(tree.path).resolve() == Path(made.worktree_dir).resolve())]
    return reasons


def census(root: Path, main: str, made: Made,
           listing: Callable[[list[str]], list[str]] = docker_listing) -> Census:
    """The census of the repository at ``root`` after a release into ``main``.

    Raises:
        ReleaseError: when ``made`` would mark what no release makes (:func:`_too_broad`).
        tools.ci.gitcmd.GitError: when git cannot read ``main`` or the worktrees.
    """
    trees = worktrees(root)
    server = dev_server(root)
    too_broad = _too_broad(made, trees, server)
    if too_broad:
        raise ReleaseError("; ".join(too_broad))
    checked_out = {tree.branch: tree.path for tree in trees if tree.branch}
    release_refs = set() if made.release_branch is None else {
        f"refs/heads/{made.release_branch}", f"refs/remotes/origin/{made.release_branch}"}
    return Census(
        git(root, "rev-parse", "--verify", f"{main}^{{commit}}").strip(),
        tuple(Marked(tree.path, _inside(tree.path, made.worktree_dir),
                     tree.branch or f"detached at {tree.head[:12]}") for tree in trees),
        tuple(Marked(ref, ref in release_refs, checked_out.get(ref))
              for ref in merged_branches(root, main)),
        tuple(Marked(name, made.database_prefix is not None
                     and name.startswith(made.database_prefix))
              for name in listing(server.listing)))
