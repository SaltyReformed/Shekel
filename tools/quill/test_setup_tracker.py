"""The tracker's setup: what it compares and what ``--apply`` may change; no GitHub calls."""
from __future__ import annotations

import copy
import re

import pytest

from tools.ci.arcs import ARCS
from tools.quill import setup_tracker
from tools.quill._github import GitHubError
from tools.quill.setup_tracker import (
    APP_PERMISSIONS,
    ISSUE_TYPES,
    LABELS,
    REPOSITORY,
    Report,
    issue_type_differences,
    label_changes,
    permission_differences,
    repository_drift,
)

#: The labels GitHub gives a new repository (measured on the 09-24 demo repository).
GITHUB_DEFAULT_LABELS = (
    "bug", "documentation", "duplicate", "enhancement", "good first issue",
    "help wanted", "invalid", "question", "wontfix",
)

_WRITES = ("POST", "PATCH", "PUT", "DELETE")


class _GitHub:
    """A fake GitHub: REST answers by (method, path), GraphQL by a callable; records calls."""

    def __init__(self, rest=None, graphql=None):
        """Hold the recorded answers."""
        self.answers = rest or {}
        self.answer_graphql = graphql
        self.calls = []
        self.mutations = []
        self.queries = []

    def rest(self, method, path, body=None):
        """Record the call; answer it, or raise the recorded error."""
        self.calls.append((method, path, body))
        answer = self.answers.get((method, path))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def graphql(self, query, **variables):
        """Record a mutation by name; answer a query from the recorded callable."""
        if query.lstrip().startswith("mutation"):
            self.mutations.append((re.search(r"\{\s*(\w+)\(", query).group(1), variables))
            return {"createProjectV2": {"projectV2": {"id": "P"}}}
        self.queries.append(query)
        return self.answer_graphql(query, **variables)

    def writes(self):
        """Every REST write and GraphQL mutation sent."""
        return [c for c in self.calls if c[0] in _WRITES] + self.mutations


def _label(name, color="ededed", description="", issues=0, pulls=0):
    """One label node as the GraphQL label query answers it."""
    return {
        "name": name,
        "color": color,
        "description": description,
        "issues": {"totalCount": issues},
        "pullRequests": {"totalCount": pulls},
    }


def _labels_github(nodes):
    """A fake GitHub whose repository carries ``nodes``."""
    return _GitHub(graphql=lambda q, **v: {"repository": {"labels": {"nodes": nodes}}})


def test_every_label_on_a_new_repository_is_replaced():
    """GitHub's nine defaults label nothing yet, so all nine go and all declared ones come."""
    changes = label_changes(LABELS, [_label(name) for name in GITHUB_DEFAULT_LABELS])
    assert changes.create == list(LABELS)
    assert not changes.correct
    assert changes.unused_extra == list(GITHUB_DEFAULT_LABELS)
    assert not changes.used_extra


def test_an_extra_label_that_labels_a_card_or_pull_request_is_never_deleted():
    """Deleting a label strips it from every card; only a label on nothing is deleted."""
    nodes = [
        *[_label(name, *LABELS[name]) for name in LABELS],
        _label("urgent", issues=1),
        _label("ci", pulls=2),
        _label("spare"),
    ]
    github = _labels_github(nodes)
    report = Report()
    setup_tracker.check_labels(github, apply=True, report=report)
    assert github.writes() == [
        ("DELETE", "/repos/saltyreformed-labs/shekel-plan/labels/spare", None)
    ]
    assert report.differences == 2
    assert any("'urgent'" in line and "labels cards" in line for line in report.lines)


def test_a_label_name_is_escaped_so_a_delete_cannot_reach_another_label():
    """``balance#old`` must not become ``DELETE .../labels/balance`` (the arc label)."""
    nodes = [
        *[_label(name, *LABELS[name]) for name in LABELS],
        _label("balance#old"),
        _label("good first issue"),
    ]
    github = _labels_github(nodes)
    setup_tracker.check_labels(github, apply=True, report=Report())
    assert [call[1].rsplit("/", 1)[1] for call in github.writes()] == [
        "balance%23old", "good%20first%20issue"
    ]


def test_check_mode_writes_no_label():
    """Without ``--apply`` every label difference is reported, none fixed."""
    github = _labels_github([_label(name) for name in GITHUB_DEFAULT_LABELS])
    report = Report()
    setup_tracker.check_labels(github, apply=False, report=report)
    assert not github.writes()
    assert report.differences == len(LABELS) + len(GITHUB_DEFAULT_LABELS)


def test_a_declared_label_with_another_colour_or_description_is_corrected():
    """GitHub answers colours in either case and a missing description as null."""
    declared = {"a": ("0e8a16", "x"), "b": ("0e8a16", ""), "c": ("0e8a16", "x")}
    actual = [_label("a", "0E8A16", "x"), _label("b", "0e8a16", None), _label("c", "ffffff", "x")]
    assert label_changes(declared, actual).correct == ["c"]


def test_every_declared_label_is_one_github_accepts():
    """GitHub takes a six-digit hex colour and a description of at most 100 characters."""
    for name, (color, description) in LABELS.items():
        assert re.fullmatch("[0-9a-f]{6}", color), name
        assert len(description) <= 100, name


def test_every_arc_is_a_label():
    """Every arc in ``tools.ci.arcs.ARCS``, the arcs' one home, is a label."""
    assert set(ARCS) <= set(LABELS)
    assert len(ARCS) == len(set(ARCS))


def test_the_filing_mark_is_a_label_the_tracker_keeps():
    """R-BAL202: every card ``quill file`` creates carries it, so ``--apply`` makes it and
    never deletes it as a label nothing declares."""
    assert setup_tracker.FILING in LABELS


def test_a_missing_repository_is_created_private_with_a_first_commit():
    """A git reference (a claim) needs a commit to point at; ``auto_init`` makes one."""
    path = "/repos/saltyreformed-labs/shekel-plan"
    created = {"node_id": "R", **REPOSITORY}
    github = _GitHub(rest={
        ("GET", path): GitHubError(404, "Not Found"),
        ("POST", "/orgs/saltyreformed-labs/repos"): created,
    })
    report = Report()
    assert setup_tracker.check_repository(github, apply=True, report=report) == created
    ((_, _, body),) = github.writes()
    assert body["auto_init"] is True
    assert body["private"] is True
    assert body["name"] == "shekel-plan"
    assert report.differences == 0


def test_check_mode_reports_a_missing_repository_and_creates_nothing():
    """Without ``--apply`` nothing is created."""
    path = "/repos/saltyreformed-labs/shekel-plan"
    github = _GitHub(rest={("GET", path): GitHubError(404, "Not Found")})
    report = Report()
    assert setup_tracker.check_repository(github, apply=False, report=report) is None
    assert not github.writes()
    assert report.differences == 1


def test_a_setting_github_did_not_take_is_still_reported_after_the_patch():
    """The PATCH's answer is re-read: a setting it ignored is not claimed as made."""
    path = "/repos/saltyreformed-labs/shekel-plan"
    wrong = {**REPOSITORY, "has_wiki": True}
    github = _GitHub(rest={("GET", path): wrong, ("PATCH", path): wrong})
    report = Report()
    setup_tracker.check_repository(github, apply=True, report=report)
    assert github.writes() == [("PATCH", path, {"has_wiki": False})]
    assert report.differences == 1


def test_a_setting_github_took_is_not_reported():
    """The PATCH's answer is what is graded, not the setting as first read."""
    path = "/repos/saltyreformed-labs/shekel-plan"
    github = _GitHub(rest={("GET", path): {**REPOSITORY, "has_wiki": True},
                           ("PATCH", path): dict(REPOSITORY)})
    report = Report()
    setup_tracker.check_repository(github, apply=True, report=report)
    assert report.differences == 0


def test_repository_drift_names_only_the_settings_that_differ():
    """A setting GitHub does not report counts as different."""
    declared = {"private": True, "has_wiki": False, "description": "d"}
    actual = {"private": True, "has_wiki": True}
    assert repository_drift(declared, actual) == {"has_wiki": False, "description": "d"}


def test_a_new_organizations_issue_types_differ_seven_ways():
    """A new organization has Task, Bug and Feature enabled and none of the four kinds."""
    defaults = [{"name": n, "is_enabled": True} for n in ("Task", "Bug", "Feature")]
    differences = issue_type_differences(ISSUE_TYPES, defaults)
    assert len(differences) == 7
    assert all(any(repr(kind) in d for d in differences) for kind in ISSUE_TYPES)


def test_the_four_kinds_enabled_and_the_defaults_disabled_match():
    """A disabled type is not a kind of card anyone can choose."""
    actual = [{"name": n, "is_enabled": True} for n in ISSUE_TYPES]
    actual += [{"name": n, "is_enabled": False} for n in ("Task", "Bug", "Feature")]
    assert not issue_type_differences(ISSUE_TYPES, actual)


def test_a_disabled_kind_is_a_difference():
    """A card cannot be given a disabled type."""
    actual = [{"name": n, "is_enabled": n != "ruling"} for n in ISSUE_TYPES]
    assert issue_type_differences(ISSUE_TYPES, actual) == ["issue type 'ruling' is disabled"]


def test_the_app_may_hold_exactly_its_declared_permissions():
    """An extra permission is as much a difference as a missing one."""
    assert not permission_differences(APP_PERMISSIONS, dict(APP_PERMISSIONS))
    extra = {**APP_PERMISSIONS, "administration": "write"}
    assert permission_differences(APP_PERMISSIONS, extra) == [
        "App permission 'administration' is 'write', declared None"
    ]
    weaker = {**APP_PERMISSIONS, "issues": "read"}
    assert permission_differences(APP_PERMISSIONS, weaker) == [
        "App permission 'issues' is 'read', declared 'write'"
    ]


def _view(index, name, view_filter, sorts):
    """An ungrouped table view as the board query answers it, showing Title and Status."""
    return {"id": f"V{index}", "name": name, "filter": view_filter, "layout": "TABLE_LAYOUT",
            "sortByFields": {"totalCount": sorts}, "groupByFields": {"totalCount": 0},
            "verticalGroupByFields": {"totalCount": 0},
            "fields": {"nodes": _visible("F1", "F2")}}


def _project(cards=0, public=False, linked=True, views=(("View 1", "", 0),)):
    """A board as GitHub makes one: a Status field and its automations."""
    return {
        "id": "P",
        "title": setup_tracker.PROJECT_TITLE,
        "public": public,
        "url": "https://github.com/orgs/o/projects/1",
        "repositories": {"nodes": [{"name": setup_tracker.REPO}] if linked else []},
        "fields": {"nodes": [
            {"id": "F1", "name": "Title", "dataType": "TITLE"},
            {"id": "F2", "name": "Status", "dataType": "SINGLE_SELECT"},
            {"id": "F3", "name": "Parent issue", "dataType": "PARENT_ISSUE"},
            {"id": "F4", "name": "Rank", "dataType": "NUMBER"},
            {},
        ]},
        "workflows": {"nodes": [
            {"name": "Item closed", "enabled": True},
            {"name": "Auto-close issue", "enabled": False},
            {"name": "Auto-add sub-issues to project", "enabled": True},
        ]},
        "views": {"nodes": [_view(index, *view) for index, view in enumerate(views, start=1)]},
        "items": {"totalCount": cards},
    }


_FIELDS = {
    "F1": {"id": "F1", "name": "Title", "dataType": "TITLE"},
    "F2": {"id": "F2", "name": "Status", "dataType": "SINGLE_SELECT"},
    "F3": {"id": "F3", "name": "Parent issue", "dataType": "PARENT_ISSUE"},
    "F4": {"id": "F4", "name": "Rank", "dataType": "NUMBER"},
}


def _visible(*ids):
    """A view's visible columns, by field id."""
    return [dict(_FIELDS[ident]) for ident in ids]


def _status_item(number, value, kind="ISSUE"):
    """One board item as the Status query answers it (``value`` None: no Status set;
    a draft has no content fields)."""
    content = {"number": number, "repository": {"name": "shekel-plan"}} if number else {}
    return {"id": f"PVTI_{number or 'draft'}", "type": kind, "content": content,
            "fieldValueByName": {"name": value} if value else None}


class _Board(_GitHub):
    """A fake GitHub holding one board (or none) that APPLIES the mutations it is sent.

    Every read returns a COPY, so a run that skipped re-reading the board after
    changing it would grade the stale copy it already held; and a board it
    CREATES is missing from the organization's list, as a fresh one can be.
    """

    def __init__(self, project, refuse=(), status_pages=((),)):
        """Hold the board, its cards' Status values page by page, and the mutations
        to refuse."""
        super().__init__(graphql=self._read)
        self.project = project
        self.refuse = refuse
        self.status_pages = [list(page) for page in status_pages]
        self.reads = []
        self.lagging = False

    def _read(self, query, **variables):
        """Answer the organization's board list, one board by id, or its cards' Status."""
        if "fieldValueByName" in query:
            page = int(variables["after"] or 0)
            more = page + 1 < len(self.status_pages)
            return {"node": {"items": {
                "nodes": self.status_pages[page],
                "pageInfo": {"hasNextPage": more, "endCursor": str(page + 1) if more else None},
            }}}
        if "node(id:" in query:
            self.reads.append(variables["id"])
            return {"node": copy.deepcopy(self.project)}
        shown = self.project and not self.lagging
        listed = [{"id": "P", "title": setup_tracker.PROJECT_TITLE}] if shown else []
        return {"organization": {"id": "O", "projectsV2": {"nodes": listed}}}

    def graphql(self, query, **variables):
        """Apply a mutation to the board, as GitHub would; answer a read."""
        if not query.lstrip().startswith("mutation"):
            return super().graphql(query, **variables)
        name = re.search(r"\{\s*(\w+)\(", query).group(1)
        self.mutations.append((name, variables))
        if name in self.refuse:
            raise GitHubError(200, f"{name} refused")
        board = self.project
        if name == "createProjectV2":
            self.project, self.lagging = _project(), True
            return {"createProjectV2": {"projectV2": {"id": "P"}}}
        if name == "updateProjectV2":
            board["public"] = False
        elif name == "linkProjectV2ToRepository":
            board["repositories"]["nodes"].append({"name": setup_tracker.REPO})
        elif name == "deleteProjectV2Field":
            board["fields"]["nodes"] = [
                f for f in board["fields"]["nodes"] if f.get("id") != variables["id"]
            ]
        elif name.endswith("View"):
            return self._view_mutation(name, variables)
        return {}

    def _view_mutation(self, name, variables):
        """Apply a view's creation or update."""
        views = self.project["views"]["nodes"]
        if name == "createProjectV2View":
            views.append(_view("NEW", variables["name"], "", 0))
            return {"createProjectV2View": {"projectV2View": {"id": "VNEW"}}}
        for view in views:
            if view["id"] != variables["id"]:
                continue
            if "fields" in variables:
                view["fields"]["nodes"] = _visible(*variables["fields"])
            else:
                view.update(name=variables["name"], filter=variables["filter"])
        return {}


def test_apply_on_an_empty_board_deletes_rank_hides_status_and_no_automation():
    """A board with no card holds nothing to lose; an automation is never deleted.

    GitHub refuses to delete its built-in Status and ignores emptying its choices
    (measured 2026-10-03), so it is left in place and hidden from the plan view;
    any other stored field is deleted.
    """
    github = _Board(_project(cards=0))
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=report)
    sent = [(name, variables.get("id")) for name, variables in github.mutations]
    assert [ident for name, ident in sent if name == "deleteProjectV2Field"] == ["F4"]
    assert not [name for name, _ in sent if name in ("deleteProjectV2Workflow",
                                                     "updateProjectV2Field")]
    assert ("updateProjectV2View", {"id": "V1", "fields": ["F1"]}) in github.mutations
    assert report.differences == 2
    assert any("'Item closed' is on" in line for line in report.lines)
    assert not any("'Auto-close issue'" in line for line in report.lines)


def test_an_automation_that_adds_cards_is_a_difference():
    """Only quill puts a card on the board (balance:R-BAL177): GitHub's default
    "Auto-add sub-issues to project" would add every finding and ruling; hiding is allowed."""
    project = _project(views=(("Plan", "is:open", 0),))
    project["fields"]["nodes"] = [_FIELDS["F1"], _FIELDS["F2"]]
    project["views"]["nodes"][0]["fields"]["nodes"] = _visible("F1")
    project["workflows"]["nodes"] = [
        {"name": "Auto-add sub-issues to project", "enabled": True},
        {"name": "Auto-add to project", "enabled": True},
        {"name": "Auto-archive items", "enabled": True},
    ]
    differences = setup_tracker.board_differences(project)
    assert [d.split(" is on")[0] for d in differences] == [
        "the board automation 'Auto-add sub-issues to project'",
        "the board automation 'Auto-add to project'",
    ]


def test_a_refused_field_deletion_is_still_a_difference():
    """If GitHub will not delete a field, the re-read board still shows it, and it counts."""
    github = _Board(_project(cards=0), refuse=("deleteProjectV2Field",))
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=report)
    assert any("stores a field, 'Rank'" in line for line in report.lines)
    # Rank, 'Item closed' on, and 'Auto-add sub-issues to project' on (R-BAL177).
    assert report.differences == 3


def test_github_status_is_tolerated_only_hidden_and_a_custom_select_is_not():
    """The undeletable Status may exist; shown in the plan view it differs, as does any other."""
    project = _project(views=(("Plan", "is:open", 0),))
    project["fields"]["nodes"] = [_FIELDS["F1"], _FIELDS["F2"]]
    project["workflows"]["nodes"] = []
    assert setup_tracker.board_differences(project) == [
        "the 'Plan' view shows stored fields ['Status']"
    ]
    project["views"]["nodes"][0]["fields"]["nodes"] = _visible("F1")
    assert not setup_tracker.board_differences(project)
    project["fields"]["nodes"].append({"id": "F5", "name": "Tier", "dataType": "SINGLE_SELECT"})
    assert setup_tracker.board_differences(project) == ["the board stores a field, 'Tier'"]


def test_every_card_holding_a_status_is_a_difference_on_every_page():
    """A Status set by hand is a second home of a card's progress; each card is named."""
    github = _Board(_project(cards=3, views=(("Plan", "is:open", 0),)),
                    status_pages=([_status_item(1, None), _status_item(2, "Todo")],
                                  [_status_item(3, "Done")]))
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=False, report=report)
    held = [line for line in report.lines if "holds Status" in line]
    assert held == ["DIFFERS card shekel-plan#2 holds Status 'Todo': clear it",
                    "DIFFERS card shekel-plan#3 holds Status 'Done': clear it"]


def test_a_draft_holding_a_status_is_a_difference_too():
    """A draft typed onto the board has no number, and is still named and reported."""
    differences = setup_tracker.status_differences(
        [_status_item(None, "Todo", kind="DRAFT_ISSUE"), _status_item(None, None, "DRAFT_ISSUE")]
    )
    assert differences == ["draft issue item PVTI_draft holds Status 'Todo': clear it"]


def test_a_renamed_status_is_a_difference():
    """Renamed, GitHub's Status cannot be read by name, so no card's Status could be checked."""
    project = _project(views=(("Plan", "is:open", 0),))
    project["fields"]["nodes"] = [_FIELDS["F1"]]
    project["workflows"]["nodes"] = []
    project["views"]["nodes"][0]["fields"]["nodes"] = _visible("F1")
    assert len(setup_tracker.board_differences(project)) == 1
    assert "renamed" in setup_tracker.board_differences(project)[0]


def test_a_plan_view_laid_out_as_a_board_or_grouped_is_a_difference():
    """A board layout's columns are a stored field; a grouping splits the drag order."""
    project = _project(views=(("Plan", "is:open", 0),))
    project["fields"]["nodes"] = [_FIELDS["F1"], _FIELDS["F2"]]
    project["workflows"]["nodes"] = []
    view = project["views"]["nodes"][0]
    view["fields"]["nodes"] = _visible("F1")
    view["layout"] = "BOARD_LAYOUT"
    view["groupByFields"]["totalCount"] = 1
    differences = setup_tracker.board_differences(project)
    assert len(differences) == 2
    assert "BOARD_LAYOUT" in differences[0] and "grouped" in differences[1]


def test_apply_sends_no_view_update_when_the_plan_view_shows_no_stored_field():
    """Hiding is only sent when there is something to hide."""
    project = _project(cards=1, views=(("Plan", "is:open", 0),))
    project["views"]["nodes"][0]["fields"]["nodes"] = _visible("F1")
    github = _Board(project)
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=Report())
    assert not [m for m in github.mutations if m[0] == "updateProjectV2View"]


def test_the_board_counts_archived_cards_as_cards():
    """An archived card keeps its field values, so it must stop a field deletion."""
    github = _Board(_project(cards=0))
    setup_tracker.check_board(github, {"node_id": "R"}, apply=False, report=Report())
    reads = [query for query in github.queries if "node(id:" in query]
    assert reads
    assert all("archivedStates: [ARCHIVED, NOT_ARCHIVED]" in query for query in reads)


def test_apply_on_a_board_holding_a_card_deletes_nothing_and_reports_instead():
    """Once a card is on the board a stored field may hold someone's work: report it."""
    github = _Board(_project(cards=1))
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=report)
    assert not [m for m in github.mutations if m[0].startswith("delete")]
    assert any("stores a field, 'Rank'" in line for line in report.lines)


def test_apply_creates_a_missing_board_and_reads_it_by_its_id():
    """A fresh board can lag in the organization's list, so it is read by the id made."""
    github = _Board(None)
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=report)
    assert github.mutations[0][0] == "createProjectV2"
    assert github.reads and set(github.reads) == {"P"}


def test_apply_makes_a_public_board_private_and_links_it():
    """Both are corrected, and the re-read board shows neither difference."""
    github = _Board(_project(cards=2, public=True, linked=False,
                             views=(("Plan", "is:open", 0),)))
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=report)
    names = [name for name, _ in github.mutations]
    assert "updateProjectV2" in names and "linkProjectV2ToRepository" in names
    assert not any("public" in line or "not linked" in line
                   for line in report.lines if line.startswith("DIFFERS"))


def test_apply_filters_a_plan_view_on_a_board_holding_cards():
    """A view holds no card data, so its filter is corrected whatever the board holds."""
    github = _Board(_project(cards=3, views=(("Plan", "", 0),)))
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=Report())
    assert ("updateProjectV2View", {"id": "V1", "name": "Plan", "filter": "is:open"}) in (
        github.mutations
    )


def test_apply_never_renames_a_view_someone_made():
    """Only a lone view still named "View 1" becomes the plan view; otherwise one is made."""
    github = _Board(_project(cards=5, views=(("Next up", "is:open label:balance", 0),
                                             ("By arc", "", 0))))
    setup_tracker.check_board(github, {"node_id": "R"}, apply=True, report=Report())
    touched = [variables.get("id") for name, variables in github.mutations
               if name == "updateProjectV2View"]
    assert [name for name, _ in github.mutations if name == "createProjectV2View"]
    assert set(touched) == {"VNEW"}
    assert [v["name"] for v in github.project["views"]["nodes"]] == ["Next up", "By arc", "Plan"]


def test_check_mode_reports_a_plan_view_with_another_filter():
    """The filter is graded, not only the view's name."""
    project = _project(views=(("Plan", "", 0),))
    project["fields"]["nodes"] = [_FIELDS["F1"], _FIELDS["F2"]]
    project["workflows"]["nodes"] = []
    project["views"]["nodes"][0]["fields"]["nodes"] = _visible("F1")
    assert setup_tracker.board_differences(project) == ["the 'Plan' view's filter is ''"]


def test_a_sorted_view_is_a_difference():
    """A sort hides the drag order, which is the plan's order."""
    project = _project(views=(("Plan", "is:open", 1),))
    project["fields"]["nodes"] = [_FIELDS["F1"], _FIELDS["F2"]]
    project["workflows"]["nodes"] = []
    project["views"]["nodes"][0]["fields"]["nodes"] = _visible("F1")
    differences = setup_tracker.board_differences(project)
    assert len(differences) == 1
    assert "sorted" in differences[0]


def test_check_mode_sends_no_mutation_even_to_a_public_unlinked_board():
    """Without ``--apply`` the run only reads, whatever it finds."""
    github = _Board(_project(cards=0, public=True, linked=False))
    report = Report()
    setup_tracker.check_board(github, {"node_id": "R"}, apply=False, report=report)
    assert not github.writes()
    # Public, unlinked, Rank stored, 'Item closed' on, 'Auto-add sub-issues to
    # project' on (R-BAL177), no 'Plan' view.
    assert report.differences == 6


def _app_setup(monkeypatch, *, owner="saltyreformed-labs", installation=None, repos=None):
    """Point ``check_app`` at a recorded App; nothing reads a key or calls GitHub."""
    monkeypatch.setattr(setup_tracker, "app_credentials", lambda: ("Iv23client", b"pem"))
    monkeypatch.setattr(setup_tracker, "app_jwt", lambda *_: "jwt")
    monkeypatch.setattr(setup_tracker, "installation_token", lambda *_: "installation")

    def installed(*_):
        if isinstance(installation, Exception):
            raise installation
        return installation

    monkeypatch.setattr(setup_tracker, "app_installation", installed)
    answers = {
        "jwt": {("GET", "/app"): {"slug": "shekel-quill", "owner": {"login": owner}}},
        "installation": {("GET", "/installation/repositories"): {
            "repositories": [{"name": name} for name in (repos or ["shekel-plan"])]
        }},
    }
    monkeypatch.setattr(setup_tracker, "GitHub", lambda token: _GitHub(rest=answers[token]))


def _installation(selection="selected"):
    """An installation as GitHub describes it, with exactly the declared permissions."""
    return {"id": 7, "repository_selection": selection, "permissions": dict(APP_PERMISSIONS)}


def test_an_app_installed_as_declared_matches(monkeypatch):
    """Registered by the organization, on the tracker only, permissions exact."""
    _app_setup(monkeypatch, installation=_installation())
    report = Report()
    setup_tracker.check_app(report)
    assert report.differences == 0


def test_an_app_that_reaches_another_repository_is_a_difference(monkeypatch):
    """The App writes cards in the tracker and nowhere else."""
    _app_setup(
        monkeypatch, installation=_installation(selection="all"), repos=["shekel-plan", "Shekel"]
    )
    report = Report()
    setup_tracker.check_app(report)
    assert report.differences == 2


def test_an_app_registered_by_another_account_is_a_difference(monkeypatch):
    """An App owned by a person's account can be installed on that account only."""
    _app_setup(monkeypatch, owner="SaltyReformed", installation=_installation())
    report = Report()
    setup_tracker.check_app(report)
    assert report.differences == 1


def test_an_app_not_yet_installed_is_a_difference_not_a_crash(monkeypatch):
    """GitHub answers 404 for an App with no installation on the organization."""
    _app_setup(monkeypatch, installation=GitHubError(404, "Not Found"))
    report = Report()
    setup_tracker.check_app(report)
    assert report.differences == 1


@pytest.mark.parametrize(
    ("differences", "refused", "code"), [(0, False, 0), (1, False, 1), (0, True, 2)]
)
def test_main_exits_by_what_it_found(monkeypatch, capsys, differences, refused, code):
    """0 when nothing differs, 1 when something does, 2 when GitHub refused a call."""
    monkeypatch.setattr(setup_tracker, "user_token", lambda: "t")
    monkeypatch.setattr(setup_tracker, "GitHub", lambda token: None)

    def repository(_github, _apply, report):
        if refused:
            raise GitHubError(500, "boom")
        for _ in range(differences):
            report.differs("x")

    monkeypatch.setattr(setup_tracker, "check_repository", repository)
    for name in ("check_issue_types", "check_board", "check_app"):
        monkeypatch.setattr(setup_tracker, name, lambda *_: None)
    assert setup_tracker.main([]) == code
    capsys.readouterr()
