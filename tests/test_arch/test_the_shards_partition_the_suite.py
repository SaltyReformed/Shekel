"""Architecture test: CI's shards run every collected test exactly once.

Plan step **bank_import:X-gy**, ledger **BI-496**.  CI splits the suite across
parallel jobs, each running the share :mod:`tests._shard` assigns it.  A split
that silently dropped a test would pass every job green while grading nothing
for that test, and one that ran a test twice would hide nothing but mean the
shards were not what they claim.  So this file grades the three claims the
module makes about itself:

1. **A partition by construction.**  For any collection and any shard count,
   every item lands in exactly one shard -- and an item one job collects that
   another does not moves no OTHER item, which is what a position-based split
   gets wrong.
2. **Every ``xdist_group`` stays whole.**  A group is a co-location contract;
   the first measured split scattered one across shards and failed
   ``test_zz_it_was_built_once_for_all_of_them``.
3. **The same answer in every process.**  The key is hashed with SHA-256, not
   the per-process-salted ``hash()``, so six CI jobs compute one split.  The
   pinned table below was cross-checked with coreutils ``sha256sum`` for two
   of its keys (2026-09-22), so it is not the implementation grading itself.

A fourth class holds ``ci.yml`` to the wiring the claims depend on: each shard
job is handed ITS OWN index (a constant would run one sixth of the suite six
times, every job green), the container tests still run there, and no workflow
brings back a second cluster configuration (ruling ``bank_import:R-BI40``).

The live suite's own partition was proven on the hosted runner: a prototype of
this module ran six shards of 15,166 items, exactly the runner's collection of
that tree with none in two (2026-09-22, run 35808935180), and THIS module's
six shards ran 15,236 items there, exactly the local collection of this tree
(run 35815066395, the pre-commit dress rehearsal).  That is the measurement;
this file is what keeps the mechanism honest after it.
"""

import re
from pathlib import Path

import pytest
import yaml

from tests._shard import SHARD_ENV, apply_shard, parse_shard, shard_key, shard_of

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


class _Item:
    """The two attributes of a pytest item the sharder reads, and nothing else."""

    def __init__(self, nodeid, *marks):
        """Hold a node id and the marks the item carries."""
        self.nodeid = nodeid
        self._marks = marks

    def iter_markers(self, name):
        """Yield this item's marks of ``name``, as ``pytest.Item`` does."""
        return (mark for mark in self._marks if mark.name == name)


class _Hook:
    """Records what ``pytest_deselected`` was told."""

    def __init__(self):
        """Start with no deselection reported."""
        self.calls = []

    def pytest_deselected(self, items):
        """Record one deselection report."""
        self.calls.append(list(items))


class _Config:
    """A config whose only used surface is ``hook.pytest_deselected``."""

    def __init__(self):
        """Carry a recording hook."""
        self.hook = _Hook()


def _group(name):
    """Return an ``xdist_group`` mark exactly as the decorator builds one."""
    return pytest.mark.xdist_group(name).mark


def _suite():
    """A synthetic collection: 240 plain items and three groups of five.

    Plain ids span twelve files so the split sees many prefixes, not one.
    """
    items = [
        _Item(f"tests/test_f{f}.py::TestC::test_{t}")
        for f in range(12) for t in range(20)
    ]
    for group in ("shekel_app_role", "recurrence_rules_ddl", "x_be_2_seeded_start_state"):
        items += [
            _Item(f"tests/test_{group}_{m}.py::test_member", _group(group))
            for m in range(5)
        ]
    return items


def _shards(items, total):
    """Run every shard of ``total`` over a fresh copy of ``items``."""
    shards = []
    for index in range(total):
        kept = list(items)
        apply_shard(_Config(), kept, f"{index}/{total}")
        shards.append(kept)
    return shards


class TestEveryItemRunsInExactlyOneShard:
    """Claim 1: the shards partition whatever was collected."""

    @pytest.mark.parametrize("total", [1, 2, 3, 6, 8])
    def test_the_shards_cover_the_collection_and_never_overlap(self, total):
        """Union is the collection; the sizes sum to it, so no item is in two.

        255 items (240 plain + 3 groups x 5): the per-shard sizes must sum to
        exactly 255 and their id sets must union to all 255 ids.
        """
        items = _suite()
        shards = _shards(items, total)
        ids = [item.nodeid for shard in shards for item in shard]
        assert len(ids) == 255
        assert sorted(ids) == sorted(item.nodeid for item in items)

    def test_an_item_only_one_job_collected_moves_no_other_item(self):
        """Adding an item changes no existing item's shard.

        A position-based split would shift every index after the new item,
        running some tests twice and others nowhere with every job green.
        """
        items = _suite()
        before = {item.nodeid: i for i, shard in enumerate(_shards(items, 6)) for item in shard}
        grown = items[:100] + [_Item("tests/test_new.py::test_only_here")] + items[100:]
        after = {item.nodeid: i for i, shard in enumerate(_shards(grown, 6)) for item in shard}
        assert {k: v for k, v in after.items() if k in before} == before
        assert "tests/test_new.py::test_only_here" in after

    def test_the_dropped_items_are_reported_as_deselected(self):
        """What a shard leaves out is reported to pytest, once, and is the rest.

        With two shards over 255 items, shard 0 keeps some and deselects the
        others; kept plus reported must be the whole 255 with no overlap.
        """
        items = _suite()
        config = _Config()
        kept = list(items)
        apply_shard(config, kept, "0/2")
        assert len(config.hook.calls) == 1
        dropped = config.hook.calls[0]
        assert len(kept) + len(dropped) == 255
        assert not {i.nodeid for i in kept} & {i.nodeid for i in dropped}

    def test_unset_deselects_nothing_and_reports_nothing(self):
        """No ``SHEKEL_TEST_SHARD`` -- every local run -- is the whole suite."""
        items = _suite()
        config = _Config()
        kept = list(items)
        apply_shard(config, kept, None)
        assert kept == items
        assert not config.hook.calls


class TestEveryGroupStaysWhole:
    """Claim 2: an ``xdist_group`` lands on one shard, every member of it."""

    @pytest.mark.parametrize("total", [2, 6, 8])
    def test_each_group_is_on_exactly_one_shard(self, total):
        """For each of the three groups, all five members share one shard."""
        shards = _shards(_suite(), total)
        for group in ("shekel_app_role", "recurrence_rules_ddl", "x_be_2_seeded_start_state"):
            holding = [
                index for index, shard in enumerate(shards)
                if any(group in item.nodeid for item in shard)
            ]
            assert len(holding) == 1, f"{group} is split across shards {holding}"
            assert sum(group in item.nodeid for item in shards[holding[0]]) == 5

    def test_the_group_name_is_read_the_way_xdist_reads_it(self):
        """First argument, else ``name=``, else ``"default"``; several are joined.

        ``--dist=loadgroup`` keys a worker by these same spellings, so the
        shard key and the worker key cannot disagree about what a group is.
        """
        by_arg = _Item("t.py::a", _group("roles"))
        by_kwarg = _Item("t.py::b", pytest.mark.xdist_group(name="roles").mark)
        bare = _Item("t.py::c", pytest.mark.xdist_group().mark)
        two = _Item("t.py::d", _group("zeta"), _group("alpha"))
        plain = _Item("t.py::e")
        assert shard_key(by_arg) == "xdist_group:roles"
        assert shard_key(by_kwarg) == "xdist_group:roles"
        assert shard_key(bare) == "xdist_group:default"
        assert shard_key(two) == "xdist_group:alpha_zeta"
        assert shard_key(plain) == "t.py::e"


class TestEveryProcessComputesTheSameSplit:
    """Claim 3: the assignment is a pure function of the key, in any process."""

    @pytest.mark.parametrize(("key", "expected"), [
        ("tests/test_routes/test_grid.py::TestGrid::test_renders", 5),
        ("tests/test_config.py::test_a", 2),
        # Cross-checked: printf '%s' KEY | sha256sum -> 866a7d8dd8bd5c12...
        # int(0x866a7d8dd8bd5c12) % 6 == 4.
        ("tests/test_config.py::test_b", 4),
        ("tests/test_config.py::test_c", 5),
        ("xdist_group:shekel_app_role", 5),
        # Cross-checked: sha256sum -> 387f0a9b20954e9e...; % 6 == 0.
        ("xdist_group:recurrence_rules_ddl", 0),
        ("tests/test_utils/test_dates.py::TestAddMonths::test_leap[2028-02-29]", 1),
        ("tests/test_models/test_x.py::test_y", 2),
    ])
    def test_the_shard_of_a_key_is_pinned(self, key, expected):
        """Eight keys pinned to their SHA-256 shard of six.

        A salted ``hash()`` would match all eight with probability (1/6)^8,
        so swapping one in fails here rather than splitting CI six ways.
        """
        assert shard_of(key, 6) == expected


class TestAMalformedShardIsRefused:
    """A job that asked for a share must get that share or stop."""

    @pytest.mark.parametrize(("spec", "expected"), [
        ("0/6", (0, 6)),
        ("5/6", (5, 6)),
        ("0/1", (0, 1)),
    ])
    def test_a_well_formed_spec_is_read(self, spec, expected):
        """``<index>/<total>`` reads as ``(index, total)``."""
        assert parse_shard(spec) == expected

    @pytest.mark.parametrize(
        "spec", ["6/6", "7/6", "1/0", "0/0", "-1/6", "a/6", "1", "", "1/6/2", " 1/6"],
    )
    def test_a_malformed_spec_is_a_usage_error(self, spec):
        """Out of range, non-numeric or mis-shaped: refused, never "run all"."""
        with pytest.raises(pytest.UsageError, match=SHARD_ENV):
            parse_shard(spec)


def _workflow(name):
    """Parse one workflow file."""
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))


class TestCIHandsEveryShardItsOwnShare:
    """The wiring in ``ci.yml`` the partition claims depend on."""

    def test_each_shard_job_is_given_its_own_index_of_the_total(self):
        """``SHEKEL_TEST_SHARD`` is the job's index of the matrix's size.

        A constant here would make every job run the SAME share -- one sixth
        of the suite, six times, all green -- so the value must come from the
        job's own position.  ``fail-fast`` is off so one red shard cannot
        cancel the rest of the report.
        """
        job = _workflow("ci.yml")["jobs"]["test"]
        run = next(step for step in job["steps"] if step["name"] == "Run tests")
        assert run["env"] == {
            SHARD_ENV: "${{ strategy.job-index }}/${{ strategy.job-total }}",
        }
        assert job["strategy"]["fail-fast"] is False
        assert len(job["strategy"]["matrix"]["shard"]) == 6

    def test_the_container_tests_run_in_ci(self):
        """``-m ""`` overrides the wrapper's local ``not docker`` default.

        Without it the 28 ``tests/test_deploy`` container tests would be
        DESELECTED in CI -- no line in any report -- where they are the only
        place they run.
        """
        job = _workflow("ci.yml")["jobs"]["test"]
        run = next(step for step in job["steps"] if step["name"] == "Run tests")["run"]
        assert run.startswith("./scripts/test.sh ")
        # The LAST ``-m`` wins in pytest, so it must be the empty one.
        assert re.findall(r'-m (""|\S+|"[^"]*")', run)[-1] == '""'


    @pytest.mark.parametrize("workflow", ["ci.yml", "calendar-sweep.yml"])
    def test_no_workflow_brings_its_own_cluster(self, workflow):
        """Every suite run in CI goes through ``scripts/test.sh``'s one cluster.

        A ``services:`` block is a second cluster configuration -- ci.yml's ran
        with ``lock_timeout`` off, the sweep's with durability on -- and ruling
        R-BI40 retired both.  So no line may invoke pytest itself on the app
        suite -- only the ``tools/`` packages, which take no database, are run
        bare -- and some step must run the suite through the wrapper.
        """
        through_wrapper = 0
        for name, job in _workflow(workflow)["jobs"].items():
            assert "services" not in job, f"{workflow}: job {name!r} brings its own cluster"
            for step in job["steps"]:
                for line in step.get("run", "").splitlines():
                    if re.search(r"(^|[\s;&|(])(python3? -m )?pytest\b", line):
                        assert "tools/" in line, (
                            f"{workflow}: {step['name']!r} runs {line.strip()!r}"
                        )
                    through_wrapper += line.lstrip().startswith("./scripts/test.sh ")
        assert through_wrapper, f"{workflow} never runs the suite through scripts/test.sh"
