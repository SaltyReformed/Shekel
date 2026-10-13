"""A release judged over throwaway repositories under tmp_path: what it carries, each rule
that refuses it, what the approval question must name, and the cut it offers.  No
network, no docker, never a real checkout."""
from __future__ import annotations

import pytest

from tools.ci.scratch import run
from tools.release._carried import STEPS_PATH, Declaration, ReleaseError
from tools.release._plan import CLEAN, NEEDS_APPROVAL, REFUSED, Judge, plan
from tools.release.conftest import migration, steps

MONEY = "Move the money. **MOVES MONEY**."
GROUP = "balance:X-bi-6-4d"
GROUP_ROW = (GROUP, "The DECOMPOSED parent of the transfer change.", "container", "--")


def _open(key: str, sentence: str = "Do it.") -> tuple[str, str, str, str]:
    """An open row."""
    return (key, sentence, "#1", "--")


def _ticked(key: str, sentence: str = "Did it.") -> tuple[str, str, str, str]:
    """A shipped row, its sentence in the past tense as a tick writes it."""
    return (key, sentence, "SHIPPED", "`0123abcd`")


def _ship(repo, onto, rows, *keys, files=None):
    """A pull request built on ``onto`` that ships ``keys`` and ticks them (``rows``, the
    whole table after the tick), merged into ``onto``: the merge, as ``dev`` takes it."""
    work = repo.commit(f"ship {' '.join(keys)}", {**(files or {}), STEPS_PATH: steps(*rows)},
                       ships=keys, parents=(onto,))
    return repo.merge(onto, work, f"Merge pull request ({' '.join(keys)})")


# -- the money flag: any version holding the row open ------------------------------------

def test_a_marker_the_tick_drops_still_reads_as_money(repo):
    """R20's shape (``969d5c141``): open with ``MOVES MONEY``, ticked without it.  Read at
    the cut it would be no money mover; read off the version that held it open, it is one."""
    base = repo.plan((_open("recurrence:R20", MONEY),), parents=())
    ship = repo.commit("ship R20", {"app.py": "new"}, ships=("recurrence:R20",),
                       parents=(base,))
    tick = repo.plan((_ticked("recurrence:R20", "Recorded the balance."),), parents=(ship,))
    assert "MOVES MONEY" not in run(repo.root, "show", f"{tick}:{STEPS_PATH}")
    (step,) = plan(repo.root, base, tick).verdict.carried.steps
    assert (step.key, step.money, step.claims) == ("recurrence:R20", True, (ship,))


def test_a_step_re_planned_to_stop_moving_money_still_reads_as_money(repo):
    """The rule's one stated over-refusal: any open version carrying the marker counts, so
    a step whose marker was removed while it was open still reads as a money mover (fails
    closed; no such re-plan in this history, measured 2026-10-13)."""
    base = repo.plan((_open("balance:A-1", MONEY),), parents=())
    replanned = repo.plan((_open("balance:A-1", "Only rename it."),), parents=(base,))
    cut = repo.plan((_ticked("balance:A-1"),), parents=(replanned,), ships=("balance:A-1",))
    (step,) = plan(repo.root, base, cut).verdict.carried.steps
    assert step.money is True


def test_a_lane_that_forked_before_the_marker_cannot_hide_a_money_mover(repo):
    """The review's history: A-1 gains its marker on dev after a lane forked; the lane edits
    steps.md holding A-1 open without it; dev ships A-1 and the money mover B-1; the lane
    merges.  Whichever open version is "newest", A-1 moves money, so two movers refuse."""
    rows = [_open("balance:A-1"), _open("balance:B-1", MONEY), _open("balance:C-1")]
    base = repo.plan(tuple(rows), parents=())
    lane = repo.plan((rows[0], rows[1], _open("balance:C-1", "Do it, renamed.")),
                     parents=(base,), subject="lane edits")
    marked = repo.plan((_open("balance:A-1", MONEY), rows[1], rows[2]), parents=(base,))
    shipped = repo.commit("ship A-1 and B-1", {STEPS_PATH: steps(
        _ticked("balance:A-1"), _ticked("balance:B-1"), rows[2])},
        ships=("balance:A-1", "balance:B-1"), parents=(marked,))
    cut = repo.commit("merge the lane", {STEPS_PATH: steps(
        _ticked("balance:A-1"), _ticked("balance:B-1"), _open("balance:C-1", "Do it, renamed."))},
        parents=(lane, shipped))
    verdict = plan(repo.root, base, cut).verdict
    assert {s.key: s.money for s in verdict.carried.steps} == {
        "balance:A-1": True, "balance:B-1": True}
    assert verdict.status == REFUSED


def test_a_side_branch_version_a_merge_took_nothing_from_is_read(repo):
    """``--full-history``: a side branch wrote the marker into A-1's open row, and the merge
    kept the first parent's steps.md, so git's default history simplification would never
    visit that version.  It held A-1 open with the marker, so A-1 moves money."""
    base = repo.plan((_open("balance:A-1"),), parents=())
    side = repo.plan((_open("balance:A-1", MONEY),), parents=(base,), subject="side")
    merged = repo.commit("merge, keeping dev's steps.md", {STEPS_PATH: steps(
        _open("balance:A-1"))}, parents=(base, side))
    cut = repo.plan((_ticked("balance:A-1"),), parents=(merged,), ships=("balance:A-1",))
    (step,) = plan(repo.root, base, cut).verdict.carried.steps
    assert step.money is True


def test_a_step_no_version_holds_open_is_refused(repo):
    """Filed already ticked in the window: no row the migration would have labelled, so
    whether it moves money cannot be read, and the release is refused."""
    base = repo.plan((), parents=())
    cut = repo.plan((_ticked("balance:A-1"),), parents=(base,), ships=("balance:A-1",))
    judged = plan(repo.root, base, cut)
    assert judged.verdict.carried.steps[0].money is None
    assert judged.verdict.status == REFUSED
    assert judged.verdict.refusals == (
        "balance:A-1: no version of steps.md from base to the cut holds it open, so "
        "whether it moves money cannot be read; the skill declares it "
        "(--declare balance:A-1=money|none@SOURCE)",)


def _born_ticked(repo, *movers):
    """A base holding ``movers`` open and moving money; then a commit filing B-1 and B-2
    already ticked and shipping them with the movers.  The base and the cut."""
    base = repo.plan(tuple(_open(key, MONEY) for key in movers), parents=())
    cut = repo.plan(tuple(_ticked(key) for key in (*movers, "balance:B-1", "balance:B-2")),
                    parents=(base,), ships=(*movers, "balance:B-1", "balance:B-2"))
    return base, cut


def test_a_declared_flag_fills_an_unreadable_step_and_the_approval_names_it(repo):
    """R-BAL207's f3: the skill declares each born-ticked step's flag with its source; the
    release is judged on them and the approval question must name each one."""
    base, cut = _born_ticked(repo)
    judged = plan(repo.root, base, cut, declared=[
        Declaration("balance:B-1", False, "a924135ed"),
        Declaration("balance:B-2", False, "HANDOFF-salary-S11.md")])
    assert [(s.key, s.money, s.declared) for s in judged.verdict.carried.steps] == [
        ("balance:B-1", False, "a924135ed"), ("balance:B-2", False, "HANDOFF-salary-S11.md")]
    assert judged.verdict.status == NEEDS_APPROVAL
    assert judged.verdict.approvals == (
        "balance:B-1: no version of steps.md from base to the cut holds it open; the skill "
        "declared it moving no money, from a924135ed",
        "balance:B-2: no version of steps.md from base to the cut holds it open; the skill "
        "declared it moving no money, from HANDOFF-salary-S11.md")


def test_a_step_declared_money_counts_as_a_mover_and_needs_its_kit(repo):
    """A declared mover beside a read one is two movers; alone, unrehearsed, it refuses."""
    base, cut = _born_ticked(repo, "balance:A-1")
    two = plan(repo.root, base, cut, declared=[Declaration("balance:B-1", True, "x"),
                                               Declaration("balance:B-2", False, "x")])
    assert two.verdict.refusals == (
        "it carries 2 steps that move money (balance:A-1, balance:B-1); a release carries "
        "one at most",)
    base, cut = _born_ticked(repo)
    kitless = plan(repo.root, base, cut, ["balance:B-1"],
                   [Declaration("balance:B-1", True, "x"), Declaration("balance:B-2", False, "x")])
    assert kitless.verdict.refusals == ("balance:B-1 moves money and has no rehearsal kit",)


@pytest.mark.parametrize("key", ["balance:A-1", "balance:Z-9"])
def test_a_declaration_for_a_readable_or_uncarried_step_judges_nothing(repo, key):
    """A declaration fills a flag no home holds; it never overrides a read one (A-1 is
    open at the base) and a typo (Z-9) never passes for one."""
    base, cut = _born_ticked(repo, "balance:A-1")
    with pytest.raises(ReleaseError, match=f"declare {key}: names no step"):
        plan(repo.root, base, cut, declared=[Declaration(key, False, "x")])


def test_a_step_claimed_twice_is_one_step_with_both_claims(repo):
    """``recurrence:R16-c-2`` shipped in three commits: one step, every claim."""
    base = repo.plan((_open("balance:A-1"),), parents=())
    first = repo.commit("part 1", {"a.py": "1"}, ships=("balance:A-1",), parents=(base,))
    second = repo.commit("part 2", {"a.py": "2"}, ships=("balance:A-1",), parents=(first,))
    (step,) = plan(repo.root, base, second).verdict.carried.steps
    assert step.claims == (second, first)


# -- two money movers --------------------------------------------------------------------

def test_two_money_movers_are_refused_and_the_newest_earlier_cut_that_passes_is_offered(repo):
    """R-BAL280's example: dev took A-1 (moves money), then B-1 (moves money), then C-1.
    Cut at C-1, two movers refuse it; the cut before B-1 refuses too; the cut after A-1
    passes and is the one offered."""
    rows = [_open("balance:A-1", MONEY), _open("balance:B-1", MONEY), _open("balance:C-1")]
    base = repo.plan(tuple(rows), parents=())
    rows[0] = _ticked("balance:A-1")
    first = _ship(repo, base, rows, "balance:A-1")
    rows[1] = _ticked("balance:B-1")
    second = _ship(repo, first, rows, "balance:B-1")
    rows[2] = _ticked("balance:C-1")
    third = _ship(repo, second, rows, "balance:C-1")
    judged = plan(repo.root, base, third)
    assert judged.verdict.status == REFUSED
    assert judged.verdict.refusals == (
        "it carries 2 steps that move money (balance:A-1, balance:B-1); a release carries "
        "one at most",)
    assert judged.passing is not None and judged.passing.cut == first
    assert [s.key for s in judged.passing.carried.steps] == ["balance:A-1"]
    assert judged.one_migration is None


def test_one_money_mover_is_clean_and_no_cut_is_offered(repo):
    """The control: one mover alone passes, and nothing earlier is judged or offered."""
    base = repo.plan((_open("balance:A-1", MONEY),), parents=())
    cut = _ship(repo, base, [_ticked("balance:A-1")], "balance:A-1")
    judged = plan(repo.root, base, cut)
    assert (judged.verdict.status, judged.passing, judged.one_migration) == (CLEAN, None, None)


def test_a_refused_cut_with_no_earlier_cut_that_passes_offers_none(repo):
    """Both movers in one pull request: no earlier state of dev passes."""
    base = repo.plan((_open("balance:A-1", MONEY), _open("balance:B-1", MONEY)), parents=())
    cut = _ship(repo, base, [_ticked("balance:A-1"), _ticked("balance:B-1")],
                "balance:A-1", "balance:B-1")
    judged = plan(repo.root, base, cut)
    assert judged.verdict.status == REFUSED and judged.passing is None


# -- the deploy-together group ---------------------------------------------------------------

def _group_base(repo, split=False):
    """A plan holding R-BAL240's group (its first leaf released on the base) and a
    container nobody labels deploy-together; with ``split``, the group's third leaf is
    itself split into 3a and 3b.  The base, and the rows as the base holds them."""
    rows = [GROUP_ROW, _open(f"{GROUP}-1"), _open(f"{GROUP}-2")]
    if split:
        rows += [(f"{GROUP}-3", "The DECOMPOSED parent of the rest.", "container", "--"),
                 _open(f"{GROUP}-3a"), _open(f"{GROUP}-3b")]
    else:
        rows.append(_open(f"{GROUP}-3"))
    rows += [("balance:X-zz", "The DECOMPOSED parent of a cleanup.", "container", "--"),
             _open("balance:X-zz-1"), _open("balance:X-zz-2")]
    start = repo.plan(tuple(rows), parents=())
    rows[1] = _ticked(f"{GROUP}-1")
    return _ship(repo, start, rows, f"{GROUP}-1"), rows


def _tick(rows, *keys):
    """``rows`` with each of ``keys`` ticked."""
    return [_ticked(row[0]) if row[0] in keys else row for row in rows]


def test_a_group_carried_in_part_is_refused_naming_the_missing_leaf(repo):
    """4d-2 without 4d-3: refused, naming 4d-3; 4d-1 shipped on the base is released.
    A container nobody labels deploy-together is shipped in part freely."""
    base, rows = _group_base(repo)
    cut = _ship(repo, base, _tick(rows, f"{GROUP}-2", "balance:X-zz-1"),
                f"{GROUP}-2", "balance:X-zz-1")
    verdict = plan(repo.root, base, cut).verdict
    (group,) = verdict.carried.groups
    assert (group.key, group.carried, group.released, group.missing) == (
        GROUP, (f"{GROUP}-2",), (f"{GROUP}-1",), (f"{GROUP}-3",))
    assert verdict.refusals == (
        f"it carries part of the deploy-together group {GROUP} ({GROUP}-2) without "
        f"{GROUP}-3; the group ships whole",)
    assert {s.key: s.groups for s in verdict.carried.steps} == {
        f"{GROUP}-2": (GROUP,), "balance:X-zz-1": ()}


def test_a_group_carried_whole_with_its_released_leaf_is_clean(repo):
    """4d-2 and 4d-3 together, 4d-1 already in production: the group ships whole."""
    base, rows = _group_base(repo)
    cut = _ship(repo, base, _tick(rows, f"{GROUP}-2", f"{GROUP}-3"), f"{GROUP}-2", f"{GROUP}-3")
    verdict = plan(repo.root, base, cut).verdict
    assert verdict.status == CLEAN
    assert verdict.carried.groups[0].missing == ()


def test_a_leaf_that_is_itself_split_counts_as_its_own_leaves(repo):
    """A split leaf is claimed by its leaves, never itself: 3a and 3b are required, 3 is
    not, and a release without 3b names 3b alone."""
    base, rows = _group_base(repo, split=True)
    whole = _ship(repo, base, _tick(rows, f"{GROUP}-2", f"{GROUP}-3a", f"{GROUP}-3b"),
                  f"{GROUP}-2", f"{GROUP}-3a", f"{GROUP}-3b")
    assert plan(repo.root, base, whole).verdict.status == CLEAN
    part = _ship(repo, base, _tick(rows, f"{GROUP}-2", f"{GROUP}-3a"),
                 f"{GROUP}-2", f"{GROUP}-3a")
    assert plan(repo.root, base, part).verdict.carried.groups[0].missing == (f"{GROUP}-3b",)


# -- migrations -------------------------------------------------------------------------------

VERSIONS = "migrations/versions"


def _migration_history(repo):
    """A base holding two migrations; dev then takes one adding b2, then one adding c3,
    editing a1 and deleting z0, and touching two files no revision lives in."""
    base = repo.commit("base", {STEPS_PATH: steps(), f"{VERSIONS}/a1_first.py":
                                migration("a1", None), f"{VERSIONS}/z0_old.py":
                                migration("z0", None)})
    first = repo.merge(base, repo.commit("add b2", {f"{VERSIONS}/b2_second.py":
                                                    migration("b2", "a1")}, parents=(base,)))
    second = repo.merge(first, repo.commit("add c3", {
        f"{VERSIONS}/c3_third.py": migration("c3", "b2"),
        f"{VERSIONS}/a1_first.py": migration("a1", None) + "# edited\n",
        f"{VERSIONS}/z0_old.py": None,
        f"{VERSIONS}/sub/not_a_revision.py": migration("x9", None),
        f"{VERSIONS}/README": "notes\n"}, parents=(first,)))
    return base, first, second


def test_each_migration_is_named_by_its_revision_and_two_added_need_approval(repo):
    """ADDED, EDITED and DELETED, each with its revision; a file outside the chain's own
    directory names none.  Two added, one edited and one deleted are named for approval,
    and the cut before the second added one is offered."""
    base, first, second = _migration_history(repo)
    judged = plan(repo.root, base, second)
    assert [(m.change, m.revision, m.path) for m in judged.verdict.migrations] == [
        ("EDITED", "a1", f"{VERSIONS}/a1_first.py"),
        ("ADDED", "b2", f"{VERSIONS}/b2_second.py"),
        ("ADDED", "c3", f"{VERSIONS}/c3_third.py"),
        ("DELETED", "z0", f"{VERSIONS}/z0_old.py"),
    ]
    assert judged.verdict.status == NEEDS_APPROVAL
    assert judged.verdict.approvals == (
        f"it adds 2 migrations: b2 ({VERSIONS}/b2_second.py), c3 ({VERSIONS}/c3_third.py)",
        f"it edited migration a1 ({VERSIONS}/a1_first.py), which is already on main and "
        "which production never re-runs",
        f"it deleted migration z0 ({VERSIONS}/z0_old.py), which is already on main and "
        "which production never re-runs",
    )
    assert judged.passing is None
    assert judged.one_migration is not None and judged.one_migration.cut == first


def test_one_added_migration_is_clean(repo):
    """The control: one added migration alone needs no extra approval line."""
    base, first, _ = _migration_history(repo)
    assert plan(repo.root, base, first).verdict.status == CLEAN


def test_a_merge_main_holds_and_dev_lacks_is_no_deletion(repo):
    """Trap (j): main took a commit dev never did.  What the release brings is the cut's
    side since the two met, so main's own migration is not read as deleted."""
    base, first, _ = _migration_history(repo)
    main = repo.commit("main only", {f"{VERSIONS}/q9_main.py": migration("q9", "a1")},
                       parents=(base,))
    assert [(m.change, m.revision) for m in plan(repo.root, main, first).verdict.migrations] == [
        ("ADDED", "b2")]


# -- what the skill says has no rehearsal kit -----------------------------------------------

def test_an_unrehearsed_money_mover_is_refused_and_the_cut_before_it_offered(repo):
    """R-BAL281's interim: a money mover with no kit refuses the release, in R-BAL280's
    shape, and the offered cut does not carry it."""
    base = repo.plan((_open("balance:A-1", MONEY), _open("balance:B-1")), parents=())
    first = _ship(repo, base, [_open("balance:A-1", MONEY), _ticked("balance:B-1")],
                  "balance:B-1")
    second = _ship(repo, first, [_ticked("balance:A-1"), _ticked("balance:B-1")],
                   "balance:A-1")
    judged = plan(repo.root, base, second, ["balance:A-1"])
    assert judged.verdict.refusals == ("balance:A-1 moves money and has no rehearsal kit",)
    assert judged.passing is not None and judged.passing.cut == first


def test_an_unrehearsed_added_migration_is_refused(repo):
    """An added migration with no kit refuses the release too; no earlier cut exists."""
    base, first, _ = _migration_history(repo)
    judged = plan(repo.root, base, first, ["b2"])
    assert judged.verdict.refusals == (
        f"migration b2 ({VERSIONS}/b2_second.py) has no rehearsal kit",)
    assert judged.passing is None


@pytest.mark.parametrize("name", ["balance:B-1", "a1", "zz"])
def test_an_unrehearsed_name_naming_no_mover_and_no_added_migration_judges_nothing(repo, name):
    """A plain step, an edited migration and a typo: each would refuse nothing, so the
    command refuses to judge rather than read as rehearsed."""
    base = repo.commit("base", {STEPS_PATH: steps(_open("balance:B-1")),
                                f"{VERSIONS}/a1_first.py": migration("a1", None)})
    cut = repo.commit("ship B-1", {STEPS_PATH: steps(_ticked("balance:B-1")),
                                   f"{VERSIONS}/a1_first.py": migration("a1", None) + "#\n"},
                      ships=("balance:B-1",), parents=(base,))
    with pytest.raises(ReleaseError, match=f"unrehearsed {name}"):
        plan(repo.root, base, cut, [name])


# -- what the pre-cutover reader cannot read ------------------------------------------------

def test_a_card_trailer_and_a_malformed_value_are_refused(repo):
    """Before the cutover a card's labels are the tracker's, and a value naming nothing
    claims nothing readable: both refuse, never pass unseen."""
    base = repo.plan((), parents=())
    cut = repo.commit("tooling", {"t.py": "1"}, trailers=("Ships: plan#4",
                                                          "Ships: banking:X-1"),
                      parents=(base,))
    refusals = plan(repo.root, base, cut).verdict.refusals
    assert refusals == (
        f"{cut[:12]} carries `Ships: plan#4`: before the cutover a card's labels are the "
        "tracker's, which this reader does not read",
        f"{cut[:12]} Ships: 'banking:X-1' is not plan#<number> (nor the old <arc>:<id> form)",
    )


def test_a_cut_holding_no_steps_md_is_after_the_cutover_and_judges_nothing(repo):
    """The post-cutover reader is piece L9a-1b; until it exists such a cut is not judged."""
    base = repo.plan((), parents=())
    cut = repo.commit("cutover", {STEPS_PATH: None}, parents=(base,))
    with pytest.raises(ReleaseError, match="after the cutover"):
        plan(repo.root, base, cut)


def test_an_empty_range_judges_nothing(repo):
    """A cut ``base`` already holds releases nothing."""
    base = repo.plan((), parents=())
    with pytest.raises(ReleaseError, match="holds no commit"):
        plan(repo.root, base, base)


def test_the_cut_before_the_second_migration_skips_a_cut_still_adding_two(repo):
    """dev took b2, then c3, then d4: cut at d4, the cut after c3 still adds two, so the
    one offered is the cut after b2."""
    base, first, second = _migration_history(repo)
    third = repo.merge(second, repo.commit("add d4", {f"{VERSIONS}/d4_fourth.py":
                                                      migration("d4", "c3")}, parents=(second,)))
    judged = plan(repo.root, base, third)
    assert [m.revision for m in judged.verdict.added] == ["b2", "c3", "d4"]
    assert judged.one_migration is not None and judged.one_migration.cut == first


def test_a_container_s_own_marker_is_no_money_flag(repo):
    """The producer's rule, not a second one: a container's leaves move money, so a claim
    naming a container whose sentence carries the marker reads as no money mover."""
    container = ("balance:X-bk", "The DECOMPOSED parent of the repair. **MOVES MONEY**.",
                 "container", "--")
    base = repo.plan((container, _open("balance:X-bk-2", MONEY)), parents=())
    cut = repo.commit("claim the container", {"a.py": "1"}, ships=("balance:X-bk",),
                      parents=(base,))
    (step,) = plan(repo.root, base, cut).verdict.carried.steps
    assert (step.key, step.money) == ("balance:X-bk", False)


def test_a_deploy_together_container_renamed_in_the_window_refuses_the_release(repo):
    """R-BAL240's key names a row at the base and none at the cut: the group cannot be read,
    so whether 4d-2 ships without its sibling cannot be told."""
    base, rows = _group_base(repo)
    renamed = [(f"{GROUP}x", *GROUP_ROW[1:]) if row == GROUP_ROW else row for row in rows]
    cut = _ship(repo, base, _tick(renamed, f"{GROUP}-2"), f"{GROUP}-2")
    assert plan(repo.root, base, cut).verdict.refusals == (
        f"{GROUP} is a deploy-together group (R-BAL240) whose row the window holds and the "
        "cut does not, so whether this release splits it cannot be read",)


def test_a_version_of_steps_md_that_does_not_parse_judges_nothing(repo):
    """The plan gate's reader refuses a malformed table by assertion; that is "nothing was
    judged" (exit 2), never a crash read as a refusal."""
    base = repo.plan((_open("balance:A-1"),), parents=())
    broken = repo.commit("wip", {STEPS_PATH: steps(_open("balance:A-1", "Do a | b."))},
                         parents=(base,))
    cut = repo.plan((_ticked("balance:A-1"),), parents=(broken,), ships=("balance:A-1",))
    with pytest.raises(ReleaseError, match=f"steps.md at {broken[:12]} does not parse"):
        plan(repo.root, base, cut)


def test_a_migration_named_with_a_non_ascii_character_is_read(repo):
    """git quotes such a path in its plain output; read NUL-separated, it is one more added
    migration, so two added need the approval question."""
    base, first, _ = _migration_history(repo)
    cut = repo.merge(first, repo.commit("add c3", {f"{VERSIONS}/c3_\u00e9.py":
                                                   migration("c3", "b2")}, parents=(first,)))
    assert [m.revision for m in plan(repo.root, base, cut).verdict.added] == ["b2", "c3"]



# -- an offered cut is judged with what the release's cut knows (the delta review) -----------

def _marked_on_a_lane(repo, also_ship=()):
    """S-1 is open with no marker at the base.  Lane P writes the marker into S-1's open
    row; lane Q ships and ticks S-1 (and ``also_ship``, each a money mover) and merges
    first; P merges after, the tick winning the row.  The base, Q's merge, P's merge."""
    rows = [_open("balance:S-1"), *(_open(key, MONEY) for key in also_ship)]
    base = repo.plan(tuple(rows), parents=())
    lane = repo.plan((_open("balance:S-1", MONEY), *rows[1:]), parents=(base,),
                     subject="lane P marks S-1")
    ticked = (_ticked("balance:S-1"), *(_ticked(key) for key in also_ship))
    first = _ship(repo, base, list(ticked), "balance:S-1", *also_ship)
    cut = repo.commit("Merge pull request (lane P)", {STEPS_PATH: steps(*ticked)},
                      parents=(first, lane))
    return base, first, cut


def test_an_offered_cut_is_refused_for_a_mover_only_the_cut_s_window_marks(repo):
    """Two movers at the cut, S-1 marked only on a lane merged after Q: the earlier cut
    carries the same two steps, and judged with the cut's knowledge it is refused too."""
    base, _, cut = _marked_on_a_lane(repo, also_ship=("balance:U-1",))
    judged = plan(repo.root, base, cut)
    assert judged.verdict.refusals == (
        "it carries 2 steps that move money (balance:S-1, balance:U-1); a release carries "
        "one at most",)
    assert judged.passing is None


def test_an_unrehearsed_mover_refuses_every_earlier_cut_carrying_it(repo):
    """The skill found no kit for S-1 at the cut; the earlier cut carries S-1, where its
    own window reads no marker.  It is refused all the same: no kit was ever judged."""
    base, _, cut = _marked_on_a_lane(repo)
    judged = plan(repo.root, base, cut, ["balance:S-1"])
    assert judged.verdict.refusals == ("balance:S-1 moves money and has no rehearsal kit",)
    assert judged.passing is None


def test_an_earlier_cut_adding_a_migration_the_cut_does_not_is_refused(repo):
    """dev took aa, then deleted it and took bb under a new id; bb has no kit.  The earlier
    cut adds aa, which the skill never saw, so it is no cut to offer."""
    base = repo.commit("base", {STEPS_PATH: steps()})
    first = repo.merge(base, repo.commit("add aa", {f"{VERSIONS}/aa_first.py":
                                                    migration("aa", None)}, parents=(base,)))
    cut = repo.merge(first, repo.commit("redo as bb", {f"{VERSIONS}/aa_first.py": None,
                                                       f"{VERSIONS}/bb_first.py":
                                                       migration("bb", None)},
                                        parents=(first,)))
    judged = plan(repo.root, base, cut, ["bb"])
    assert judged.verdict.refusals == (f"migration bb ({VERSIONS}/bb_first.py) has no "
                                       "rehearsal kit",)
    assert judged.passing is None
    candidate = Judge(repo.root, base, cut, ((), ())).verdict(first, "add aa")
    assert candidate.refusals == (
        f"it adds migration aa ({VERSIONS}/aa_first.py), which the release's cut does not "
        "add, so no rehearsal of it was judged",)


def test_an_earlier_cut_is_held_to_the_group_s_leaves_at_the_cut(repo):
    """4d-1 and 4d-2 shipped, then 4d-3 was filed: the cut is refused without 4d-3, and so
    is every earlier cut carrying the first two, though 4d-3 did not exist there yet."""
    rows = [GROUP_ROW, _open(f"{GROUP}-1"), _open(f"{GROUP}-2"), _open("balance:C-1")]
    base = repo.plan(tuple(rows), parents=())
    rows = _tick(rows, f"{GROUP}-1", f"{GROUP}-2")
    first = _ship(repo, base, rows, f"{GROUP}-1", f"{GROUP}-2")
    rows.append(_open(f"{GROUP}-3"))
    filed = repo.merge(first, repo.plan(tuple(rows), parents=(first,), subject="file 4d-3"))
    cut = _ship(repo, filed, _tick(rows, "balance:C-1"), "balance:C-1")
    judged = plan(repo.root, base, cut)
    assert judged.verdict.carried.groups[0].missing == (f"{GROUP}-3",)
    assert judged.passing is None


def test_a_step_filed_under_two_names_is_read_under_both(repo):
    """pay_calendar:C9 says MOVES MONEY and names recurrence:R9 as the same step, whose own
    row does not: a claim on R9 is a money mover, so with A-1 two movers refuse."""
    base = repo.plan((("pay_calendar:C9", MONEY, "#1", "--", "recurrence:R9"),
                      _open("recurrence:R9"), _open("balance:A-1", MONEY)), parents=())
    cut = repo.plan((("pay_calendar:C9", "Did it.", "SHIPPED", "`0123abcd`", "recurrence:R9"),
                     _ticked("recurrence:R9"), _ticked("balance:A-1")), parents=(base,),
                    ships=("recurrence:R9", "balance:A-1"))
    assert {s.key: s.money for s in plan(repo.root, base, cut).verdict.carried.steps} == {
        "balance:A-1": True, "recurrence:R9": True}


def test_a_step_declared_twice_judges_nothing(repo):
    """Two declarations of one step would drop one value unseen."""
    base, cut = _born_ticked(repo)
    with pytest.raises(ReleaseError, match="declared twice"):
        plan(repo.root, base, cut, declared=[Declaration("balance:B-1", True, "x"),
                                             Declaration("balance:B-1", False, "y")])
