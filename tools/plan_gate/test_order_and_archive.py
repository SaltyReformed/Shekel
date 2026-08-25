"""Controls for the EXECUTION ORDER arms and the archive banner.

Split out of ``test_registry_integrity.py`` when that module reached its
1000-line ceiling, on the same ruling that split ``_order.py`` and
``_archive.py`` out of ``_registry.py``: an over-ceiling module is split,
never shaved (findings N-152 / N-156).  The staging fixtures are imported
from the sibling rather than copied -- two spellings of "plant a defect in a
registry copy" would let the two suites disagree about what they are
grading, which is the denormalization these registries exist to remove.
"""
from __future__ import annotations

import pytest

import _archive as archive
import _order as order
import _registry as registry
from _staging import row_of, stage_a_live_container, with_cell


class TestTheOrderIsATotalOrderTheGraphAllows:
    """conventions.md rule 14, the arm the ordered document exists for."""

    def test_the_live_order_is_clean(self):
        """The live order is clean."""
        assert not order.rank_violations()

    def test_the_live_corpus_actually_has_an_order_to_grade(self):
        """A rule with no subject is untested by the clean case.

        Three premises, because each arm below is blind without one: there ARE
        ranked rows, there ARE containers held out of the order, and at least
        one ranked row is blocked by unshipped work -- without the third, the
        graph-consistency arm grades nothing at all.
        """
        rows = registry.step_rows()
        steps = {row.key: row for row in rows}
        assert sum(1 for row in rows if row.rank is not None) >= 50
        assert any(row.is_container for row in rows), "no container to hold out"
        assert any(
            row.rank is not None
            and any(
                key in steps and not steps[key].shipped
                for key in row.blocked_keys()
            )
            for row in rows
        ), "no ranked row waits on unshipped work -- arm 4 grades nothing"

    def test_the_control_fires_when_a_rank_precedes_its_own_blocker(self, stage):
        """The defect this rule exists for: a stated order the graph forbids."""
        line = row_of("steps", "| balance | X-f4 |")
        stage("steps", line, with_cell(line, 4, "#1"))
        problems = order.rank_violations()
        assert any("balance:X-f4" in p and "forbids" in p for p in problems), problems

    def test_the_control_fires_on_a_hole_in_the_sequence(self, stage):
        """A gap makes "the first row that is not done" ambiguous.

        **Both the subject and the expected hole are DERIVED, and the previous
        spelling of this control had stopped discriminating.**  It staged
        ``balance:X-ap`` -- which has since SHIPPED, so it carries no rank and
        moving it to ``#999`` opened holes at the TAIL rather than the one it
        named -- and then asserted the substring ``"has no #2"``, which is
        satisfied by ``"has no #234"``.  A control that any hole above #199
        passes is not grading the hole it says it is.  Staging the FIRST ranked
        row and expecting the rank it VACATED makes the assertion exact.
        """
        subject = min(
            (row for row in registry.step_rows() if row.rank is not None),
            key=lambda row: row.rank,
        )
        vacated = subject.rank
        line = row_of("steps", f"| {subject.arc} | {subject.ident} |")
        stage("steps", line, with_cell(line, 4, "#999"))
        problems = order.rank_violations()
        assert any(
            f"has no #{vacated}." in p for p in problems
        ), problems

    def test_the_control_fires_on_an_unparseable_order_cell(self, stage):
        """A row a reader cannot place in the sequence."""
        line = row_of("steps", "| balance | X-am |")
        stage("steps", line, with_cell(line, 4, "soon"))
        problems = order.rank_violations()
        assert any("balance:X-am" in p and "'soon'" in p for p in problems), problems

    def test_two_unrelated_steps_may_not_share_one_rank(self, stage):
        """A rank repeats only where two names are ONE commit.

        The rank it collides WITH is derived, for the reason the sibling
        controls give: ``#26`` was a stored copy of a value the live table
        decides, so a renumbering silently moved which row this collided with
        while the assertion went on passing.
        """
        rows = [row for row in registry.step_rows() if row.rank is not None]
        subject = next(
            row for row in rows if row.key == "balance:X-ad-b"
        )
        collide_with = next(
            row.rank for row in rows
            if row.rank != subject.rank
            and row.key not in subject.alias_keys()
        )
        line = row_of("steps", f"| {subject.arc} | {subject.ident} |")
        stage("steps", line, with_cell(line, 4, f"#{collide_with}"))
        assert any("not one identity class" in p for p in order.rank_violations())

    def test_an_identity_class_sharing_one_rank_is_not_a_violation(self, stage):
        """`C5a` and `R-F10` are one commit at one position, and that is legal.

        **The class SHIPPED at `pay_calendar:C2-b2` (`fe365de1`), so it no
        longer holds a rank and the clean corpus stops exercising the
        exemption.**  It is staged back onto a rank rather than deleted with
        it: the exemption is still the rule, the next identity class to be
        ranked will need it, and a reader deleting it would otherwise learn
        nothing from a passing suite.  The pair is the real one, read out of
        the Shipped table, so this still rests on a relation the corpus states
        rather than on a fixture.
        """
        rows = {row.key: row for row in registry.step_rows()}
        left, right = rows["pay_calendar:C5a"], rows["recurrence:R-F10"]
        assert left.shipped and right.shipped
        assert right.key in left.alias_keys()
        assert left.key in right.alias_keys()

        # A rank NO live row holds, so the only thing under test is whether two
        # names at one rank are exempt.  The density arm fires on the hole that
        # leaves, which is why this reads one arm's messages and not all.
        free = max(r.rank for r in rows.values() if r.rank is not None) + 2
        for row in (left, right):
            line = row_of("steps", f"| {row.arc} | {row.ident} |")
            stage("steps", line, with_cell(line, 4, f"#{free}"))
        shared = [p for p in order.rank_violations() if "identity class" in p]
        assert not shared, shared


class TestTheStartsCellIsDerivedAndReconciled:
    """conventions.md rule 14: a stored derived value with its reconciler."""

    def test_the_live_starts_column_is_clean(self):
        """The live starts column is clean."""
        assert not order.starts_violations()

    def test_the_live_corpus_contains_both_states_to_grade(self):
        """Both a READY row and a WAITING row exist, or one arm is blind."""
        heads = [row.blocked.split(" / ")[0].strip() for row in registry.step_rows()
                 if not row.shipped]
        assert any(head == "NOW" for head in heads), "no ready row"
        assert any(head.startswith("after #") for head in heads), "no waiting row"
        assert any(head.startswith("ticks with #") for head in heads), "no container"

    def test_the_control_fires_on_a_stale_now(self, stage):
        """A stale NOW sends a reader at work they cannot start."""
        line = row_of("steps", "| balance | X-f4 |")
        stage("steps", line, with_cell(line, 6, "NOW / balance:X-f3"))
        problems = order.starts_violations()
        assert any("balance:X-f4" in p and "stale NOW" in p for p in problems), problems

    def test_the_control_fires_when_the_named_rank_is_not_the_latest(self, stage):
        """Naming an earlier blocker understates the wait.

        **The rank it stages and the rank it expects are both DERIVED**, and
        that is this suite's own subject applied to itself: the literals ``#5``
        and ``#32`` were a stored copy of a value the live table decides, and
        they rotted on the first commit that renumbered it.
        """
        line = row_of("steps", "| credit_card | CC0a |")
        stage("steps", line, with_cell(
            line, 6, "after #1 / balance:X-f4 / balance:X-am",
        ))
        latest = max(
            order.rank_map()[key] for key in ("balance:X-f4", "balance:X-am")
        )
        problems = order.starts_violations()
        assert any(
            "credit_card:CC0a" in p and f"#{latest}" in p for p in problems
        ), problems

    def test_the_control_fires_when_a_ready_row_claims_to_be_blocked(self, stage):
        """A stale `after` hides work the reader can pick up today.

        **The blocker's SHIPPED-ness is ASSERTED, not assumed.**  A shipped step
        carries no rank, so `waits` is empty for it -- and equally empty for a
        blocker that does not exist at all.  Without the premise the control
        passed unchanged against `balance:X-NONEXISTENT`, proving the arm fires
        on a typo rather than on the state it names.

        **The SUBJECT is derived too, and it had to be.**  This named
        `balance:X-ap` until that step shipped, at which point the arm skipped
        the row and the control stopped firing -- the identical rot its sibling
        above records for the literals ``#5`` and ``#32``, one column over: a
        stored copy of a value the live table decides.  The state the control
        needs is "an open step every one of whose blockers has shipped", which
        the table can be asked for.
        """
        rows = {row.key: row for row in registry.step_rows()}
        subject = next(
            row for row in registry.step_rows()
            if not row.shipped and not row.is_container
            and row.blocked_keys()
            and all(
                key in rows and rows[key].shipped for key in row.blocked_keys()
            )
        )
        line = row_of("steps", f"| {subject.arc} | {subject.ident} |")
        stage("steps", line, with_cell(
            line, 6, f"after #4 / {' / '.join(subject.blocked_keys())}",
        ))
        problems = order.starts_violations()
        assert any(
            subject.key in p and "has SHIPPED" in p for p in problems
        ), problems

    def test_the_control_fires_on_a_container_naming_the_wrong_leaf(self, stage):
        """A container ticks with its LAST leaf, not an earlier one."""
        line = row_of("steps", "| balance | X-i |")
        stage("steps", line, with_cell(line, 6, "ticks with #1"))
        ticks = order.rank_map()["balance:X-i"]
        problems = order.starts_violations()
        assert any("balance:X-i" in p and f"#{ticks}" in p for p in problems), problems

    def test_the_control_fires_on_a_container_whose_leaves_are_a_siblings(self, stage):
        """A container's leaves may be filed under an identity SIBLING's name.

        **This arm was BLIND until 2026-08-11.**  `balance:X-l`,
        `pay_calendar:C2` and `recurrence:R-F12` are ONE step under three names,
        and every leaf of the class is a `pay_calendar:C2-*` row -- so a per-arc
        leaf derivation answered eight keys for one member and NOTHING for the
        other two, both of which then fell through the `if leaf_ranks` guard.
        No commit ever held the split state: a working-tree renumber produced
        one identity class stating TWO tick ranks and every gate stayed green
        over it, which is the equally damning and accurate version.
        """
        members = stage_a_live_container(stage)
        parent = members[0]
        ticks = order.rank_map()[parent]
        arc, ident = parent.split(":", 1)
        line = row_of("steps", f"| {arc} | {ident} |")
        stage("steps", line, with_cell(line, 6, f"ticks with #{ticks + 1}"))
        problems = order.starts_violations()
        assert any(
            parent in p and f"#{ticks}" in p for p in problems
        ), problems

    def test_the_control_fires_on_a_one_way_alias_cell(self, stage):
        """Class membership is UNDIRECTED, or a blanked cell re-opens the hole.

        The class's declared parent carries no arc-local leaf, so its tick rank
        comes entirely from the class.  Read the class off the parent's own `also` cell alone
        and blanking that cell restores the exact blindness the sibling control
        above grades -- a stale tick rank with every arm green.  Measured on a
        staged copy 2026-08-11: 1 problem with the class intact, 0 without.
        """
        members = stage_a_live_container(stage)
        parent = members[0]
        ticks = order.rank_map()[parent]
        arc, ident = parent.split(":", 1)
        line = row_of("steps", f"| {arc} | {ident} |")
        staged = with_cell(with_cell(line, 6, f"ticks with #{ticks + 1}"), 2, "--")
        stage("steps", line, staged)
        problems = order.starts_violations()
        assert any(
            parent in p and f"#{ticks}" in p for p in problems
        ), problems

    def test_the_ready_count_matches_the_table(self):
        """steps.md's THIRD self-count, and the one rule 3's arms did not reach."""
        assert not order.ready_count_violation()

    def test_the_control_fires_on_a_stale_ready_count(self, stage):
        """A stale ready count tells a cold reader how much work is available.

        It is its OWN arm rather than more of the step/open/graph counts,
        because it moves for a different reason than any of them: a row becomes
        ready when its last blocker SHIPS, which changes neither the size, the
        open count nor the graph. Measured 2026-08-11: the sentence read 38
        against a table holding 41, and no arm had an opinion.
        """
        ready = sum(
            1 for row in registry.step_rows()
            if not row.shipped and not row.is_container
            and row.blocked.split(" / ")[0].strip() == "NOW"
        )
        stage(
            "steps",
            f"{ready} of these steps are legal to start right now",
            f"{ready - 3} of these steps are legal to start right now",
        )
        problems = order.ready_count_violation()
        assert any("are legal to start right" in p for p in problems), problems

    def test_the_ready_pattern_still_matches_the_live_document(self):
        """A pattern matching nothing reads as 'no count is claimed' and passes."""
        assert order.READY_COUNT_RX.search(registry.STEPS.read_text())

    def test_the_control_fires_on_an_unparseable_head(self, stage):
        """Every other spelling of readiness used to read as legal."""
        line = row_of("steps", "| balance | X-am |")
        stage("steps", line, with_cell(line, 6, "whenever"))
        problems = order.starts_violations()
        assert any("balance:X-am" in p and "'whenever'" in p for p in problems), problems


class TestEveryStepSaysWhatItIsInOneSentence:
    """conventions.md rule 14: the 38-row truncation class."""

    def test_the_live_descriptions_are_clean(self):
        """The live descriptions are clean."""
        assert not order.description_violations()

    @pytest.mark.parametrize("truncated", [
        "the DECOMPOSED parent,",
        "`feat(cash): the ledger is sum-of-postings` -- **THE",
        "**RULED as the follow-on, not an",
        "one balanced",
    ])
    def test_the_control_fires_on_a_real_truncation(self, stage, truncated):
        """Each specimen is a VERBATIM cell from the index this rule replaced.

        Reconstructed rather than invented: a synthetic fragment would prove
        the pattern matches something, not that it matches the defect.
        """
        line = row_of("steps", "| balance | X-f3 |")
        stage("steps", line, with_cell(line, 3, truncated))
        problems = order.description_violations()
        assert any("balance:X-f3" in p and "TRUNCATED" in p for p in problems), problems

    def test_the_control_fires_on_an_empty_description(self, stage):
        """Every step says what it is."""
        line = row_of("steps", "| balance | X-f3 |")
        stage("steps", line, with_cell(line, 3, "--"))
        assert any("no description" in p for p in order.description_violations())

    def test_the_control_fires_over_the_cap(self, stage):
        """A specification that will not fit belongs in the arc document."""
        line = row_of("steps", "| balance | X-f3 |")
        stage("steps", line, with_cell(line, 3, "word " * 100 + "end."))
        problems = order.description_violations()
        assert any("against a" in p and "cap" in p for p in problems), problems

    def test_the_cap_is_a_forcing_function_and_not_sized_to_fit(self):
        """Rule 4: a cap with no headroom has already stopped forcing anything.

        The inverse matters too and is why the floor is here: a cap raised far
        above the longest live cell grades nothing, which is how a limit
        becomes decoration.
        """
        longest = max(len(row.title.strip()) for row in registry.step_rows())
        assert longest < order.DESCRIPTION_CAP, "the live table is over its cap"
        assert order.DESCRIPTION_CAP < longest * 2, (
            f"the cap ({order.DESCRIPTION_CAP}) is more than double the "
            f"longest live description ({longest}) and forces nothing"
        )


class TestAnArchivedDocumentSaysSoOnItsFirstLine:
    """conventions.md rule 15."""

    def test_every_archived_document_carries_the_banner(self):
        """Every archived document carries the banner."""
        assert not archive.archive_banner_violations()

    def test_the_archive_actually_holds_documents_to_grade(self):
        """A floor far under the live count, so a broken walk is visible.

        **The floor came DOWN from 100 to 40 on 2026-08-11, and the reason is
        the point**: 100 sat just under a 124-document archive, so it was
        really tracking the corpus size rather than proving the walk works.
        Deleting an archived document is now an EXPECTED operation -- 25 went
        that day, referenced by nothing -- and a floor set just under the
        current count turns every such deletion into a red suite. The floor's
        only job is catching a walk that has stopped finding files at all.
        """
        assert len(list(archive.archived_docs())) >= 40

    def test_the_walk_covers_every_archived_tree(self):
        """Three separate trees hold archived documents, and all three count.

        ``docs/plans/historical`` and ``docs/historical`` are the ones a
        directory walk rooted at the balance arc's archive would miss, and the
        document that started this rule was reachable from none of them.
        """
        parents = {str(p.relative_to(registry.REPO).parent)
                   for p in archive.archived_docs()}
        for tree in ("docs/audits/balance_architecture/archive",
                     "docs/historical",
                     "docs/plans/historical"):
            assert tree in parents, f"{tree} is not being walked"

    def test_the_control_fires_on_a_document_that_does_not_say_it_is_archived(
        self, tmp_path, monkeypatch,
    ):
        """The defect: an archived plan a session reads as the plan of record."""
        fake = tmp_path / "docs" / "plans" / "historical"
        fake.mkdir(parents=True)
        (fake / "implementation_plan_something.md").write_text(
            "# Implementation Plan: Something\n\nThis is the plan of record.\n",
        )
        monkeypatch.setattr(registry, "REPO", tmp_path)
        problems = archive.archive_banner_violations()
        assert len(problems) == 1, problems
        assert "implementation_plan_something.md" in problems[0]

    def test_a_live_document_outside_an_archive_is_not_graded(
        self, tmp_path, monkeypatch,
    ):
        """The negative control: the arm keys on the DIRECTORY, not the name."""
        live = tmp_path / "docs" / "plans"
        live.mkdir(parents=True)
        (live / "implementation_plan_something.md").write_text("# Live\n")
        monkeypatch.setattr(registry, "REPO", tmp_path)
        assert not archive.archive_banner_violations()


# The CAP arms moved here from ``test_registry_integrity`` on 2026-08-14, when
# that module reached pylint's 1,000-line ceiling and this project's ruling on
# an over-ceiling module is that it SPLITS (findings N-152 / N-156).  They fit:
# a cap is rule 4 and the only legal way back under one is rule 5's ARCHIVE,
# which is what the rest of this module grades.

class TestEveryRegistryIsUnderItsCap:
    """conventions.md rule 4, on the documents it did not used to reach.

    **``ledger.md`` LEFT this class on 2026-08-25, by developer ruling**, and
    :class:`TestTheLedgerIsBOUNDEDRatherThanCAPPED` is what replaced it.  A
    line cap on a registry holding ONE LINE PER MEASURED DEFECT is a cap on how
    many defects the project may have measured; it was raised three times, and
    the fourth time it bound, a finding was written into a code docstring to get
    around it.  The arms it kept are the ROW cap (a row may not swell into the
    arc document's argument) and a runaway backstop.
    """

    @pytest.mark.parametrize("name", sorted(registry.REGISTRY_CAPS))
    def test_the_registry_is_within_its_line_cap(self, name):
        """The registry is within its line cap."""
        problems = [p for p in registry.registry_line_cap_violations()
                    if p.startswith(name)]
        assert not problems, problems[0]

    @pytest.mark.parametrize("name", sorted(registry.REGISTRY_CAPS))
    def test_the_cap_still_has_headroom(self, name):
        """A cap already binding cannot absorb the next finding."""
        cap = registry.REGISTRY_CAPS[name]
        actual = len((registry.PLANS / name).read_text().splitlines())
        assert actual <= cap - 20, (
            f"{name} is at {actual} of {cap} -- under 20 lines of headroom. "
            f"conventions.md rule 5: archive a completed span, do not raise the cap"
        )

    def test_the_control_fires_when_a_registry_grows_past_its_cap(self, tmp_path,
                                                                 monkeypatch):
        """A cap nobody has seen fail is a number, not a gate.

        EVERY registry is staged, not just the one being pushed over: the arm
        walks them all, so a directory holding one file raises
        ``FileNotFoundError`` and the control fails for a reason that has
        nothing to do with the cap.

        The subject was ``ledger.md`` until 2026-08-25, when its line cap was
        dropped; it is ``steps.md`` now, which is a registry the arm still
        holds.
        """
        over = "steps.md"
        for name, cap in registry.REGISTRY_CAPS.items():
            padding = cap + 1 if name == over else 1
            (tmp_path / name).write_text("filler\n" * padding)
        monkeypatch.setattr(registry, "PLANS", tmp_path)
        problems = registry.registry_line_cap_violations()
        assert len(problems) == 1, problems
        assert problems[0].startswith(over) and "rule 4" in problems[0]


class TestTheLedgerIsBOUNDEDRatherThanCAPPED:
    """What replaced ``ledger.md``'s line cap (developer ruling 2026-08-25).

    Three arms, and the split between them is the ruling: a row may not become
    a specification (graded elsewhere, ``LEDGER_ROW_CAP``); a table larger than
    any real backlog is an accident (graded here); and the backlog itself is
    REPORTED rather than gated, because refusing to record a measured defect is
    what the dropped cap did.
    """

    def test_the_ledger_carries_no_line_cap(self):
        """The ruling, asserted -- a re-added cap must be a decision, not a merge."""
        assert "ledger.md" not in registry.REGISTRY_CAPS, (
            "ledger.md's line cap was dropped 2026-08-25; putting it back is a "
            "developer ruling, not something a merge does quietly"
        )

    def test_the_real_ledger_is_under_the_runaway_backstop(self):
        """The live file, so the backstop is a fact rather than a constant."""
        assert registry.ledger_runaway_violation() is None

    def test_the_backstop_fires_on_a_table_that_could_only_be_an_accident(
        self, monkeypatch,
    ):
        """A backstop nobody has seen fail is a number, not a gate."""
        monkeypatch.setattr(registry, "LEDGER_RUNAWAY_ROWS", 1)
        violation = registry.ledger_runaway_violation()
        assert violation is not None
        assert "runaway backstop" in violation

    def test_the_backlog_is_reported_per_arc_and_sums_to_the_table(self):
        """The signal the cap was standing in for, and it must be complete.

        Summing it against the row count is what stops the report drifting
        into a partial view of the pile it exists to keep visible.
        """
        by_arc = registry.open_findings_by_arc()

        assert by_arc, "a ledger with rows reports a backlog"
        assert sum(count for _, count in by_arc) == len(registry.ledger_rows())
        assert by_arc == sorted(by_arc, key=lambda pair: (-pair[1], pair[0]))


class TestTheLedgerStatesItsBACKLOG:
    """The by-arc split, which is what the dropped line cap was standing in for.

    Developer ruling 2026-08-25: ``ledger.md`` carries no line cap, and the
    forcing function moved onto the number instead of the file.  A gate may not
    refuse to record a defect somebody has measured -- it did, twice in three
    days, and the second time a finding went into a code docstring to get round
    it.  What is graded now is that the pile is stated TRUTHFULLY where every
    reader of the file meets it.
    """

    def test_the_stated_split_matches_the_table(self):
        """The live file."""
        assert registry.stated_arc_counts_violation() is None

    def test_the_control_fires_on_a_stale_split(self, stage):
        """A number nobody grades is a number that goes stale.

        Staged on the REAL file, so the control exercises the same parser on
        the same shape the live document uses.
        """
        stage("ledger", "By arc: balance 155", "By arc: balance 154")

        violation = registry.stated_arc_counts_violation()

        assert violation is not None
        assert "154" in violation and "155" in violation

    def test_the_control_fires_when_the_split_is_deleted(self, stage):
        """Deleting the sentence must fail loudly, not read as agreement."""
        stage("ledger", "By arc: balance 155", "By nothing at all: balance 155")

        assert registry.stated_arc_counts_violation() is not None

    def test_a_line_wrap_inside_the_sentence_is_not_a_disagreement(self, stage):
        """The formatter re-wraps this prose, and it once split an arc's count.

        ``rumdl`` normalises paragraphs to 100 characters, and one such wrap
        landed between ``bank_import`` and its number -- which the first parser
        read as that arc having gone missing, on a sentence that was true.  A
        gate whose answer depends on where a formatter broke a line is a gate
        that fails for the wrong reason.
        """
        stage("ledger", "recurrence 20, bank_import", "recurrence 20,\nbank_import")

        assert registry.stated_arc_counts_violation() is None


class TestTheOrderTableIsSorted:
    """conventions.md rule 14: the table is SORTED, and holds only positions.

    **The arm whose absence let the live document drift.** Every other order
    arm grades the ``order`` COLUMN and none reads where a row physically
    SITS, so the column stayed perfect while the file stopped being sorted --
    `#16` above `#15`, three recurrence rows at `#81`-`#83` wedged between
    `#15` and `#17`, and three SHIPPED rows inside the order table. The gate
    passed 200/200 the whole time.
    """

    def test_the_live_order_table_is_sorted(self):
        """The live table reads in rank order and holds nothing else."""
        assert not order.row_order_violations()

    def test_the_live_corpus_has_enough_rows_to_grade(self):
        """A rule with no subject is untested by the clean case above.

        Two premises: several ranked rows exist, so "ascending" can fail; and
        unranked rows exist somewhere in the file, so "contiguous" is a real
        constraint rather than one nothing could violate.
        """
        rows = registry.step_rows()
        assert sum(1 for row in rows if row.rank is not None) >= 50
        assert any(row.rank is None for row in rows), (
            "no unranked row anywhere -- the contiguity arm grades nothing"
        )

    def test_the_control_fires_when_a_row_sits_below_a_higher_rank(self, stage):
        """The defect: a sorted-looking table whose first row is not next."""
        line = row_of("steps", "| balance | X-f4 |")
        stage("steps", line, with_cell(line, 4, "#90"))
        problems = order.row_order_violations()
        assert any(
            "balance:X-f4" in p and "sits BELOW" in p for p in problems
        ), problems

    def test_the_control_fires_on_an_unranked_row_inside_the_order(self, stage):
        """A SHIPPED row left in the order table is not a position.

        This is the live defect the arm was written for: three of them sat in
        the order table -- ``bank_import:X-f6a-4``, ``balance:X-au-c3`` and
        ``recurrence:R-F17`` -- while every column-grading arm passed.
        """
        line = row_of("steps", "| balance | X-f4 |")
        stage("steps", line, with_cell(line, 4, "SHIPPED"))
        problems = order.row_order_violations()
        assert any(
            "balance:X-f4" in p and "INSIDE the order table" in p
            for p in problems
        ), problems
