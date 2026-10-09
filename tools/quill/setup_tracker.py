"""Set up the private tracker Shekel's plan lives in, and report where it differs from this file.

The plan moves out of ``docs/plans/`` into a private repository's Issues
(ruling ``balance:R-BAL170``): each step, finding, ruling and question is one
issue -- a "card" -- and one Project board holds the open ones in the order
the developer drags them.  This module is the one home of WHERE that tracker
lives (:class:`Place`: :data:`PLAN`, and :data:`REHEARSAL`, the throwaway one
X-cx's migration is rehearsed on) and of its CONFIGURATION: the repository and
its settings, the labels, the board, and the things only the web can set (the
organization's issue types, which need a scope the shared token does not carry;
the board's automations and its view's sort, which the API cannot change; and
quill's GitHub App).  The cards
themselves are content, and their home is the tracker; nothing here names one.

Run with no flag it changes nothing: it reads GitHub, prints one line per
thing it checked, and exits 1 if anything differs.  ``--apply`` makes what is
missing and corrects what drifted, then reports what it could not fix (an
issue type, a board automation or a view's sort with the page to fix it on).
It DELETES only what nothing depends on: a label no issue or pull request
carries, and a custom board field that stores a value per card while the
board holds no card, archived ones counted.  Every other difference is
reported for a person to settle, so a re-run never destroys anyone's work.

**One home per fact (rule 14) is why the board stores no field.**  A card's
order is the board's own drag order (``items(orderBy: {field: POSITION})``),
its kind is its issue type, its arc one label, whether it shipped is git's
answer (``Ships: plan#N``).  A field holding any of those would be a second
home, so every custom field that stores a value is a difference.  GitHub's
built-in ``Status`` field is the one exception GitHub forces: it refuses to
delete it ("Only custom fields can be deleted.") and silently ignores an
update emptying its choices (both measured 2026-10-03).  So, by the
developer's ruling that night, it stays UNUSED: hidden from the plan view,
every automation that writes it switched off, and any card holding a Status
value reported as a difference.  Those automations matter twice over: one of
them, "Auto-close issue", would close a card when someone set its Status to
Done -- a third way to close a card, beside git and the developer's own hand.
**Nor may an automation put a card on the board** (ruling ``balance:R-BAL177``):
the board holds steps and questions only, and quill places each one
itself, a new leaf where the step it splits sat.  "Auto-add sub-issues to
project" would put every finding and ruling, each a sub-issue of its owner
step, at the bottom of the ordered list (measured 2026-10-04: within ten
seconds).  The board may keep only an automation that hides a card it already
holds (:data:`ALLOWED_WORKFLOWS`).

Usage, from the repository root::

    python -m tools.quill.setup_tracker            # check only
    python -m tools.quill.setup_tracker --apply    # make and correct
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import quote

from tools.ci.arcs import ARCS
from tools.quill._github import (
    APP_DIR,
    GitHub,
    GitHubError,
    app_credentials,
    app_installation,
    app_jwt,
    installation_token,
    user_token,
)


@dataclass(frozen=True)
class Place:
    """Where a tracker lives: its organization, its repository and its board's title.

    Every reader compares a place's names as whole TOKENS, never as text: the
    rehearsal's repository name begins with the real one (``shekel-plan`` is a
    prefix of ``shekel-plan-rehearsal``), so a substring or prefix test would
    read the one as the other.
    """

    owner: str
    name: str
    board_title: str

    @property
    def full_name(self) -> str:
        """``owner/name``: how GitHub names the repository in a link (``nameWithOwner``)."""
        return f"{self.owner}/{self.name}"

    @property
    def path(self) -> str:
        """The repository's REST path, ``/repos/owner/name``."""
        return f"/repos/{self.full_name}"


#: The tracker the plan lives in (ruling ``balance:R-BAL170``): the one place
#: this module configures and every quill command reads and writes.
PLAN = Place("saltyreformed-labs", "shekel-plan", "Shekel plan")

#: The throwaway tracker X-cx's migration (L7) is rehearsed on before L8 runs it
#: on :data:`PLAN`.  Its name holds :data:`PLAN`'s, which is why a place's names
#: are compared as tokens.
REHEARSAL = Place("saltyreformed-labs", "shekel-plan-rehearsal", "Shekel plan rehearsal")

#: :data:`PLAN`'s names, which this module's checks configure.
ORG, REPO, PROJECT_TITLE = PLAN.owner, PLAN.name, PLAN.board_title

#: The four kinds of card.  The organization's issue types are managed only
#: with the ``admin:org`` scope, which the token every session shares does not
#: carry, so the developer sets them on the web and this module checks them.
ISSUE_TYPES = ("step", "finding", "ruling", "question")
ISSUE_TYPES_URL = f"https://github.com/organizations/{ORG}/settings/issue-types"

#: The repository's settings this module holds.  ``auto_init`` is a creation
#: argument, not a setting: the README it commits gives the repository a first
#: commit, without which GitHub refuses to create any git reference -- and a
#: claim on a card is one (``claims/N``).
REPOSITORY = {
    "private": True,
    "description": "Shekel's plan: each step, finding, ruling and question is one card.",
    "has_issues": True,
    "has_wiki": False,
}

#: Each arc is one label.  The arcs are :data:`tools.ci.arcs.ARCS`, their one
#: home beside each arc's planning document (step X-cx's L4 deleted this
#: module's second list); a label's colour is the one at its arc's position.
_ARC_COLORS = ("0e8a16", "1d76db", "5319e7", "d93f0b", "006b75", "c5a100")

#: The mark every card quill files carries from the call that creates it
#: until its filing's last write removes it (ruling ``balance:R-BAL202``).
FILING = "filing"

#: The two labels a release reads: a step whose release moves money, and a parent
#: whose leaves ``/release`` ships in one release.  ``quill file step --label`` and X-cx's
#: migration name them here.
MOVES_MONEY_LABEL, DEPLOY_TOGETHER_LABEL = "moves-money", "deploy-together"

#: Every label the tracker carries: one per arc, the two a release reads, and
#: quill's :data:`FILING` mark.
LABELS = {
    **{
        arc: (_ARC_COLORS[index % len(_ARC_COLORS)], f"The {arc.replace('_', '-')} arc")
        for index, arc in enumerate(ARCS)
    },
    MOVES_MONEY_LABEL: (
        "b60205", "Its release moves money: /release carries at most one such step"
    ),
    DEPLOY_TOGETHER_LABEL: (
        "fbca04", "A parent whose leaves /release ships in one release, never split"
    ),
    FILING: (
        "bfdadc",
        "Quill has not finished filing this card, so it is not offered as work",
    ),
}

#: What quill's App may do, and nothing more: write cards (issues,
#: their sub-issues and dependencies), create and delete the git references
#: that are claims (contents), and place cards on the organization's board.
#: ``metadata: read`` is mandatory for every App.
APP_PERMISSIONS = {
    "contents": "write",
    "issues": "write",
    "metadata": "read",
    "organization_projects": "write",
}

#: What quill reads of the repositories a board is linked to (:func:`linked`).
LINKED_REPOSITORIES = "repositories(first: 100) { totalCount nodes { nameWithOwner } }"

#: A board field of one of these types STORES a value per card; every other
#: type (title, labels, parent issue, sub-issue progress, ...) shows a fact
#: the card itself holds.
STORED_FIELD_TYPES = frozenset(
    {"TEXT", "SINGLE_SELECT", "MULTI_SELECT", "NUMBER", "DATE", "ITERATION"}
)

#: GitHub's built-in field, the one stored field GitHub will not let a board
#: lose (see the module docstring); it is tolerated only while unused.
GITHUB_STATUS_FIELD = "Status"

#: The board's automations that may run: only one that hides a card the board
#: already holds.  Names as GitHub's documentation lists them (the API lists a
#: built-in automation only once a board has it).  Every other one writes a
#: stored field or an issue's state, or ADDS cards ("Auto-add to project",
#: "Auto-add sub-issues to project"), which only quill may do
#: (``balance:R-BAL177``); each must be switched off -- on the web, because the
#: API can only DELETE an automation, and what deleting a built-in one does
#: is unmeasured.
ALLOWED_WORKFLOWS = frozenset({"Auto-archive items"})

#: The board's view: the open cards, unsorted, so it shows the drag order.
VIEW_NAME = "Plan"
VIEW_FILTER = "is:open"
#: The name GitHub gives a new board's one view (measured on the 09-24 demo board).
GITHUB_DEFAULT_VIEW = "View 1"

_PROJECT_FIELDS = """
  id number title public url
  """ + LINKED_REPOSITORIES + """
  fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name dataType } } }
  workflows(first: 50) { nodes { name enabled } }
  views(first: 20) { nodes {
    id name filter layout sortByFields(first: 1) { totalCount }
    groupByFields(first: 1) { totalCount } verticalGroupByFields(first: 1) { totalCount }
    fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name dataType } } }
  } }
  items(first: 1, archivedStates: [ARCHIVED, NOT_ARCHIVED]) { totalCount }
"""

_PROJECTS = """
query($org: String!) {
  organization(login: $org) { id projectsV2(first: 100) { nodes { id title } } }
}"""

_PROJECT = "query($id: ID!) { node(id: $id) { ... on ProjectV2 {" + _PROJECT_FIELDS + "} } }"

_STATUS_VALUES = (
    """
query($id: ID!, $after: String) {
  node(id: $id) { ... on ProjectV2 {
    items(first: 100, after: $after, archivedStates: [ARCHIVED, NOT_ARCHIVED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id type
        content {
          ... on Issue { number repository { name } }
          ... on PullRequest { number repository { name } }
        }
        fieldValueByName(name: \""""
    + GITHUB_STATUS_FIELD
    + """\") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
      }
    }
  } }
}"""
)

_LABELS = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    labels(first: 100) {
      nodes { name color description issues { totalCount } pullRequests { totalCount } }
    }
  }
}"""


@dataclass
class Report:
    """What a run found and did; ``differences`` counts what is still wrong."""

    lines: list[str] = field(default_factory=list)
    differences: int = 0

    def ok(self, what: str) -> None:
        """Record something that matches this file."""
        self.lines.append(f"ok      {what}")

    def made(self, what: str) -> None:
        """Record something ``--apply`` created, corrected or deleted."""
        self.lines.append(f"made    {what}")

    def differs(self, what: str) -> None:
        """Record a difference this run did not fix."""
        self.differences += 1
        self.lines.append(f"DIFFERS {what}")


@dataclass
class LabelChanges:
    """How the repository's labels differ from :data:`LABELS`."""

    create: list[str]
    correct: list[str]
    unused_extra: list[str]
    used_extra: list[str]


def label_changes(declared: dict, actual: list[dict]) -> LabelChanges:
    """Compare declared labels with the repository's.

    ``actual`` is the GraphQL label nodes: ``name``, ``color``,
    ``description`` and the counts of issues and pull requests carrying it,
    open and closed.  A label this file does not declare is ``unused_extra``
    when nothing carries it (deleting it loses nothing) and ``used_extra``
    otherwise.
    """
    by_name = {label["name"]: label for label in actual}
    create = [name for name in declared if name not in by_name]
    correct = [
        name
        for name, (color, description) in declared.items()
        if name in by_name
        and (by_name[name]["color"].lower(), by_name[name]["description"] or "")
        != (color, description)
    ]
    unused_extra, used_extra = [], []
    for name, label in by_name.items():
        if name in declared:
            continue
        carried = label["issues"]["totalCount"] + label["pullRequests"]["totalCount"]
        (used_extra if carried else unused_extra).append(name)
    return LabelChanges(create, correct, unused_extra, used_extra)


def repository_drift(declared: dict, actual: dict) -> dict:
    """The declared settings the repository does not hold, with their declared values."""
    return {key: value for key, value in declared.items() if actual.get(key) != value}


def issue_type_differences(declared: tuple[str, ...], actual: list[dict]) -> list[str]:
    """Each way the organization's issue types differ from ``declared``.

    Every declared type must exist and be enabled; any OTHER enabled type is a
    kind of card the plan cannot read, so it is a difference too.
    """
    by_name = {issue_type["name"]: issue_type for issue_type in actual}
    differences = []
    for name in declared:
        if name not in by_name:
            differences.append(f"issue type {name!r} is missing")
        elif not by_name[name]["is_enabled"]:
            differences.append(f"issue type {name!r} is disabled")
    differences += [
        f"issue type {name!r} is enabled but is not one of the plan's kinds"
        for name, issue_type in by_name.items()
        if name not in declared and issue_type["is_enabled"]
    ]
    return differences


def permission_differences(declared: dict, actual: dict) -> list[str]:
    """Each permission the App's installation holds that differs from ``declared``.

    An EXTRA permission is a difference: the App gets what it needs and no more.
    """
    return [
        f"App permission {name!r} is {actual.get(name)!r}, declared {declared.get(name)!r}"
        for name in sorted(set(declared) | set(actual))
        if declared.get(name) != actual.get(name)
    ]


def linked(place: Place, repositories: dict) -> bool | None:
    """Whether a board's linked ``repositories`` (read as :data:`LINKED_REPOSITORIES`) hold
    ``place``'s repository, by its whole ``owner/name``: the one test, for this module's
    check and quill's refusal to write to an unlinked board.

    ``True`` when the read names it, which proves the link however short the read; ``False``
    when a WHOLE read (``totalCount`` nodes) does not; ``None`` when a read GitHub cut short
    does not, which says nothing either way: the check reports it as unread,
    ``--apply`` sends no link for it, and quill refuses the board.
    """
    names = [node["nameWithOwner"] for node in repositories["nodes"]]
    if place.full_name in names:
        return True
    return False if repositories["totalCount"] <= len(names) else None


def stores_values(board_field: dict) -> bool:
    """Whether a board field holds a value per card."""
    return bool(board_field) and board_field["dataType"] in STORED_FIELD_TYPES


def is_github_status(board_field: dict) -> bool:
    """Whether a field is GitHub's built-in Status, which no board can delete."""
    return (board_field["name"], board_field["dataType"]) == (GITHUB_STATUS_FIELD, "SINGLE_SELECT")


def _item_name(item: dict) -> str:
    """A board item as a person finds it: ``repository#number``, or its kind and id.

    A draft (typed straight onto the board) has no repository or number, and a
    REDACTED item is one the reader may not see.
    """
    content = item["content"] or {}
    if "number" in content:
        return f"card {content['repository']['name']}#{content['number']}"
    return f"{item['type'].lower().replace('_', ' ')} item {item['id']}"


def status_differences(items: list[dict]) -> list[str]:
    """One difference per board item holding a Status value, drafts and all
    (``items``: the status query's nodes)."""
    return [
        f"{_item_name(item)} holds {GITHUB_STATUS_FIELD} "
        f"{item['fieldValueByName']['name']!r}: clear it"
        for item in items
        if (item["fieldValueByName"] or {}).get("name")
    ]


def board_differences(project: dict) -> list[str]:
    """Each way a board's configuration differs from this file (its cards aside)."""
    differences = []
    if project["public"]:
        differences.append("the board is public")
    link = linked(PLAN, project["repositories"])
    if link is False:
        differences.append(f"the board is not linked to {PLAN.full_name}")
    elif link is None:
        differences.append(
            f"the board links {project['repositories']['totalCount']} repositories and the "
            f"read holds {len(project['repositories']['nodes'])}, none of them "
            f"{PLAN.full_name}: "
            "whether it is linked is unread")
    differences += [
        f"the board stores a field, {board_field['name']!r}"
        for board_field in project["fields"]["nodes"]
        if stores_values(board_field) and not is_github_status(board_field)
    ]
    if not any(is_github_status(f) for f in project["fields"]["nodes"] if f):
        differences.append(
            f"no single-select field is named {GITHUB_STATUS_FIELD!r}: GitHub's built-in one "
            "was renamed, so no card's Status can be read -- rename it back"
        )
    differences += [
        f"the board automation {workflow['name']!r} is on, and only "
        f"{sorted(ALLOWED_WORKFLOWS)} may be: switch it off at {project['url']}/workflows"
        for workflow in project["workflows"]["nodes"]
        if workflow["enabled"] and workflow["name"] not in ALLOWED_WORKFLOWS
    ]
    views = [view for view in project["views"]["nodes"] if view["name"] == VIEW_NAME]
    if not views:
        differences.append(f"the board has no view named {VIEW_NAME!r}")
    for view in views:
        if view["filter"] != VIEW_FILTER:
            differences.append(f"the {VIEW_NAME!r} view's filter is {view['filter']!r}")
        if view["sortByFields"]["totalCount"]:
            differences.append(
                f"the {VIEW_NAME!r} view is sorted, which hides the drag order: remove its "
                f"sort at {project['url']}"
            )
        shown = [f["name"] for f in view["fields"]["nodes"] if stores_values(f)]
        if shown:
            differences.append(f"the {VIEW_NAME!r} view shows stored fields {shown}")
        if view["layout"] != "TABLE_LAYOUT":
            differences.append(
                f"the {VIEW_NAME!r} view is a {view['layout']}, whose columns are a stored "
                f"field: make it a table at {project['url']}"
            )
        if view["groupByFields"]["totalCount"] or view["verticalGroupByFields"]["totalCount"]:
            differences.append(
                f"the {VIEW_NAME!r} view is grouped, which splits the drag order: remove its "
                f"grouping at {project['url']}"
            )
    return differences


def allowed_workflow_states(project: dict) -> list[str]:
    """Each automation the board may run (:data:`ALLOWED_WORKFLOWS`) and whether it is on.

    Either state matches this file, so neither is a difference; the line says which,
    because a board copied from this one (X-cx's rehearsal tracker) must match it.  GitHub
    lists a built-in automation only once a board has it, so one it does not list is said
    to be unlisted, not off.
    """
    listed = {workflow["name"]: workflow["enabled"] for workflow in project["workflows"]["nodes"]}
    return [
        f"board automation {name!r} may run: it is {'on' if listed[name] else 'off'}"
        if name in listed else
        f"board automation {name!r} may run: GitHub lists no such automation on this board"
        for name in sorted(ALLOWED_WORKFLOWS)
    ]


def check_repository(github: GitHub, apply: bool, report: Report) -> dict | None:
    """Check (and with ``apply``, create or correct) the tracker repository."""
    try:
        actual = github.rest("GET", PLAN.path)
    except GitHubError as error:
        if error.status != 404:
            raise
        if not apply:
            report.differs(f"repository {ORG}/{REPO} does not exist")
            return None
        actual = github.rest(
            "POST", f"/orgs/{ORG}/repos", {"name": REPO, "auto_init": True, **REPOSITORY}
        )
        report.made(f"repository {ORG}/{REPO}")
    drift = repository_drift(REPOSITORY, actual)
    if drift and apply:
        actual = github.rest("PATCH", PLAN.path, drift)
        report.made(f"sent repository settings {sorted(drift)}")
        drift = repository_drift(REPOSITORY, actual)
    if drift:
        report.differs(f"repository settings differ: {drift}")
    else:
        report.ok(f"repository {ORG}/{REPO} and its settings")
    return actual


def check_labels(github: GitHub, apply: bool, report: Report) -> None:
    """Check (and with ``apply``, make, correct or delete) the repository's labels."""
    nodes = github.graphql(_LABELS, owner=ORG, name=REPO)["repository"]["labels"]["nodes"]
    changes = label_changes(LABELS, nodes)
    base = f"{PLAN.path}/labels"
    for name in changes.create:
        if apply:
            color, description = LABELS[name]
            github.rest("POST", base, {"name": name, "color": color, "description": description})
            report.made(f"label {name}")
        else:
            report.differs(f"label {name} is missing")
    for name in changes.correct:
        if apply:
            color, description = LABELS[name]
            github.rest(
                "PATCH",
                f"{base}/{quote(name, safe='')}",
                {"color": color, "description": description},
            )
            report.made(f"label {name} colour and description")
        else:
            report.differs(f"label {name} has another colour or description")
    for name in changes.unused_extra:
        if apply:
            github.rest("DELETE", f"{base}/{quote(name, safe='')}")
            report.made(f"deleted label {name!r} (it labelled nothing)")
        else:
            report.differs(f"label {name!r} is not declared here (it labels nothing)")
    for name in changes.used_extra:
        report.differs(f"label {name!r} is not declared here and labels cards: settle it by hand")
    if not (changes.create or changes.correct or changes.unused_extra or changes.used_extra):
        report.ok(f"labels: exactly the {len(LABELS)} declared")


def check_issue_types(github: GitHub, report: Report) -> None:
    """Check the organization's issue types (set on the web; see :data:`ISSUE_TYPES`)."""
    differences = issue_type_differences(
        ISSUE_TYPES, github.rest("GET", f"/orgs/{ORG}/issue-types")
    )
    for difference in differences:
        report.differs(f"{difference}: set it at {ISSUE_TYPES_URL}")
    if not differences:
        report.ok(f"issue types: exactly {', '.join(ISSUE_TYPES)} enabled")


def _project(github: GitHub, project_id: str) -> dict:
    """One board, read by its id (a fresh board can lag in the organization's list)."""
    return github.graphql(_PROJECT, id=project_id)["node"]


def _board_listing(github: GitHub, place: Place) -> tuple[str, str | None]:
    """The organization's node id and the id of the board titled ``place``'s board title."""
    organization = github.graphql(_PROJECTS, org=place.owner)["organization"]
    title = place.board_title
    matches = [p for p in organization["projectsV2"]["nodes"] if p["title"] == title]
    if len(matches) > 1:
        raise GitHubError(200, f"{len(matches)} boards are titled {title!r}; keep one")
    return organization["id"], (matches[0]["id"] if matches else None)


def find_board(github: GitHub, place: Place) -> str | None:
    """The node id of ``place``'s board (None while there is none): the one lookup both
    this module and quill make."""
    return _board_listing(github, place)[1]


def _find_project(github: GitHub) -> tuple[str, dict | None]:
    """The organization's node id and the board titled :data:`PROJECT_TITLE`, if any."""
    org_id, board_id = _board_listing(github, PLAN)
    return org_id, (_project(github, board_id) if board_id else None)


def _apply_to_view(github: GitHub, project: dict, report: Report) -> None:
    """Give the board its :data:`VIEW_NAME` view, filtered to open cards.

    A view someone made for another purpose is never renamed: only a board's
    lone view still carrying GitHub's default name becomes the plan view, and
    any other board gets a new one.
    """
    views = project["views"]["nodes"]
    named = [view for view in views if view["name"] == VIEW_NAME]
    if named:
        view = named[0]
    elif len(views) == 1 and views[0]["name"] == GITHUB_DEFAULT_VIEW:
        view = views[0]
    else:
        created = github.graphql(
            "mutation($p: ID!, $name: String!) { createProjectV2View(input: {projectId: $p, "
            "name: $name, layout: TABLE_LAYOUT}) { projectV2View { id } } }",
            p=project["id"],
            name=VIEW_NAME,
        )
        view = {"id": created["createProjectV2View"]["projectV2View"]["id"], "name": VIEW_NAME,
                "filter": ""}
        report.made(f"board view {VIEW_NAME!r}")
    if (view["name"], view["filter"]) != (VIEW_NAME, VIEW_FILTER):
        github.graphql(
            "mutation($id: ID!, $name: String!, $filter: String!) { updateProjectV2View("
            "input: {viewId: $id, name: $name, filter: $filter}) { clientMutationId } }",
            id=view["id"],
            name=VIEW_NAME,
            filter=VIEW_FILTER,
        )
        report.made(f"board view {view['name']!r} -> {VIEW_NAME!r} ({VIEW_FILTER})")


def _hide_stored_fields(github: GitHub, project: dict, report: Report) -> None:
    """Hide every stored field from the plan view, keeping its other columns."""
    for view in project["views"]["nodes"]:
        visible = view["fields"]["nodes"]
        if view["name"] != VIEW_NAME or not any(map(stores_values, visible)):
            continue
        github.graphql(
            "mutation($id: ID!, $fields: [ID!]!) { updateProjectV2View(input: {viewId: $id, "
            "configuration: {visibleFieldIds: $fields}}) { clientMutationId } }",
            id=view["id"],
            fields=[f["id"] for f in visible if f and not stores_values(f)],
        )
        report.made(f"hid {[f['name'] for f in visible if stores_values(f)]} from the "
                    f"{VIEW_NAME!r} view")


def _apply_to_board(github: GitHub, project: dict, repository: dict, report: Report) -> None:
    """Make a board private, link it and name and filter its view; while it holds no
    card, delete every custom field that stores a value per card."""
    if project["public"]:
        github.graphql(
            "mutation($id: ID!) { updateProjectV2(input: {projectId: $id, public: false}) "
            "{ clientMutationId } }",
            id=project["id"],
        )
        report.made("board made private")
    if linked(PLAN, project["repositories"]) is False:
        github.graphql(
            "mutation($p: ID!, $r: ID!) { linkProjectV2ToRepository("
            "input: {projectId: $p, repositoryId: $r}) { clientMutationId } }",
            p=project["id"],
            r=repository["node_id"],
        )
        report.made(f"board linked to {REPO}")
    _apply_to_view(github, project, report)
    if project["items"]["totalCount"]:
        return
    for board_field in project["fields"]["nodes"]:
        if not stores_values(board_field) or is_github_status(board_field):
            continue
        try:
            github.graphql(
                "mutation($id: ID!) { deleteProjectV2Field(input: {fieldId: $id}) "
                "{ clientMutationId } }",
                id=board_field["id"],
            )
        except GitHubError as error:
            report.lines.append(f"refused deleting board field {board_field['name']!r}: {error}")
            continue
        report.made(f"deleted board field {board_field['name']!r} (the board held no card)")


def _cards_holding_status(github: GitHub, project_id: str) -> list[str]:
    """One difference per card on the board, archived ones included, holding a Status."""
    differences, after = [], None
    while True:
        items = github.graphql(_STATUS_VALUES, id=project_id, after=after)["node"]["items"]
        differences += status_differences(items["nodes"])
        if not items["pageInfo"]["hasNextPage"]:
            return differences
        after = items["pageInfo"]["endCursor"]


def check_board(github: GitHub, repository: dict | None, apply: bool, report: Report) -> None:
    """Check (and with ``apply``, create and correct) the organization's board."""
    org_id, project = _find_project(github)
    if project is None:
        if not apply or repository is None:
            report.differs(f"the board {PROJECT_TITLE!r} does not exist")
            return
        created = github.graphql(
            "mutation($owner: ID!, $title: String!, $repo: ID!) { createProjectV2("
            "input: {ownerId: $owner, title: $title, repositoryId: $repo}) "
            "{ projectV2 { id } } }",
            owner=org_id,
            title=PROJECT_TITLE,
            repo=repository["node_id"],
        )
        report.made(f"board {PROJECT_TITLE!r}")
        project = _project(github, created["createProjectV2"]["projectV2"]["id"])
    if apply and repository is not None:
        _apply_to_board(github, project, repository, report)
        project = _project(github, project["id"])
        _hide_stored_fields(github, project, report)
        project = _project(github, project["id"])
    differences = board_differences(project) + _cards_holding_status(github, project["id"])
    for difference in differences:
        report.differs(difference)
    for state in allowed_workflow_states(project):
        report.ok(state)
    if not differences:
        report.ok(
            f"board {PROJECT_TITLE!r} ({project['url']}): no stored field but GitHub's unused "
            f"{GITHUB_STATUS_FIELD}, no automation on that writes or adds cards, an unsorted "
            f"{VIEW_NAME!r} table of open cards, ungrouped, showing no stored field"
        )


def check_app(report: Report) -> None:
    """Check quill's App: registered by the organization, installed on the tracker only."""
    try:
        client_id, private_key = app_credentials()
    except FileNotFoundError:
        report.differs(f"the App's credentials are not in {APP_DIR} (app.json, app.pem)")
        return
    token = app_jwt(client_id, private_key, int(time.time()))
    app = GitHub(token).rest("GET", "/app")
    if app["owner"]["login"] != ORG:
        report.differs(f"the App {app['slug']!r} is registered by {app['owner']['login']}")
    try:
        installation = app_installation(ORG, token)
    except GitHubError as error:
        if error.status != 404:
            raise
        report.differs(f"the App {app['slug']!r} is not installed on {ORG}")
        return
    differences = permission_differences(APP_PERMISSIONS, installation["permissions"])
    if installation["repository_selection"] != "selected":
        differences.append("the App is installed on ALL of the organization's repositories")
    # The whole installation, never narrowed to one repository: this check is what
    # sees every repository the App reaches.
    scoped = GitHub(installation_token(installation["id"], token, repository=None))
    names = [r["name"] for r in scoped.rest("GET", "/installation/repositories")["repositories"]]
    if names != [REPO]:
        differences.append(f"the App reaches {names}, not only {REPO}")
    for difference in differences:
        report.differs(difference)
    if not differences:
        report.ok(f"App {app['slug']!r}: installed on {REPO} only, permissions as declared")


def main(argv: list[str] | None = None) -> int:
    """Run the checks (and with ``--apply``, the fixes); 0 when nothing differs."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="make and correct, not only check")
    apply = parser.parse_args(argv).apply
    github = GitHub(user_token())
    report = Report()
    try:
        repository = check_repository(github, apply, report)
        if repository is not None:
            check_labels(github, apply, report)
        check_issue_types(github, report)
        check_board(github, repository, apply, report)
        check_app(report)
    except GitHubError as error:
        print("\n".join(report.lines))
        print(f"GitHub refused: {error}", file=sys.stderr)
        return 2
    print("\n".join(report.lines))
    print(f"{report.differences} difference(s)")
    return 1 if report.differences else 0


if __name__ == "__main__":
    sys.exit(main())
