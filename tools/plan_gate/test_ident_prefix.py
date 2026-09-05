"""The gate over rule 10's ARC PREFIXES (developer ruling 2026-09-05).

Its own module rather than a class in :mod:`test_registry_integrity`, for the
same mechanical reason :mod:`_ident_prefix` is its own module: that suite
stands at 951 lines against pylint's 1000-line ``max-module-lines``, and this
package holds a 10.00/10 floor with ``--fail-under=10``.

**Every predicate here has a negative control that is shown to fire.**  The
rule is a CARVE-OUT in TWO directions -- the seven families that predate it are
not graded, and an unrecognised family IS -- so each direction is mutated
separately and the fail sets are measured rather than asserted in prose.  The
first draft of this arm's predecessor claimed disjoint fail sets it did not
have, and an adversarial review found it by mutation; the controls below assert
on the STAGED KEY alone for that reason, never on a total count.
"""
from __future__ import annotations

import re

import _ident_prefix as ident_prefix
import _registry as registry
from _staging import a_live_ledger_row, row_of, with_cell


def _arc_of(prefix: str) -> str:
    """Return the arc named by a ``| arc | id |`` row prefix."""
    return prefix.strip("| ").split(" | ", maxsplit=1)[0]


def _staged(problems: list[str], arc: str, ident: str) -> list[str]:
    """Return only the messages about the row a control staged.

    A control that counts ALL problems is also asserting every OTHER row is
    clean, which couples it to the carve-out and makes two directions fail
    together that were meant to fail apart.

    Args:
        problems: What the arm returned.
        arc: The staged row's arc.
        ident: The staged id.

    Returns:
        The subset naming ``<arc>:<ident>``.
    """
    return [p for p in problems if p.startswith(f"{arc}:{ident}:")]


class TestANewLedgerIdCarriesItsArcsPrefix:
    """conventions.md rule 10's prefix scheme (developer ruling 2026-09-05).

    Why the ruling exists, and why reserved numeric RANGES were rejected first,
    is :mod:`_ident_prefix`; this grades that it holds.
    """

    @staticmethod
    def _a_prefix_other_than(arc: str) -> tuple[str, str]:
        """Return another arc's name and the prefix it mints with."""
        for other, prefix in sorted(ident_prefix.ARC_PREFIXES.items()):
            if other != arc:
                return other, prefix
        raise AssertionError("ARC_PREFIXES holds fewer than two arcs")

    def test_no_prefixed_id_exists_yet_so_the_next_arm_is_vacuous(self):
        """State the premise the arm cannot: green means nothing is graded YET.

        Every id in the corpus is still of a family that predates the ruling,
        so :func:`ledger_id_prefix_violations` returns clean because it
        inspected NOTHING -- not because the corpus complies.  Asserted rather
        than left in prose, so the day it stops being true this arm says so and
        its sibling starts meaning something.
        """
        minted = [
            row.key for row in registry.ledger_rows()
            if (m := ident_prefix.PREFIXED_ID_RX.match(row.bare_ident))
            and m["prefix"] not in ident_prefix.LEGACY_HYPHENATED_FAMILIES
        ]
        assert not minted, (
            f"ids now carry arc prefixes ({minted}); delete this arm, because "
            "the one below is no longer vacuous"
        )

    def test_every_prefixed_id_names_its_own_arc(self):
        """The live corpus obeys the rule.

        Vacuous today -- the arm above is what says so.
        """
        problems = ident_prefix.ledger_id_prefix_violations()
        assert not problems, problems[0]

    def test_the_gate_and_the_convention_state_the_same_prefixes(self):
        """The prefixes live in two homes, and this is the reconciler.

        The developer ruled six prefixes into ``conventions.md`` rule 10 and
        the gate enforces six from a dict.  Without this arm an arc dropped
        from either, or a prefix respelled in one, is invisible to every other
        arm -- rule 14's own shape, on the change that introduces the pair.
        """
        text = registry.CONVENTIONS.read_text()
        marker = "**A NEW finding id CARRIES ITS ARC'S PREFIX**"
        assert marker in text, (
            f"conventions.md rule 10 no longer states the scheme with {marker!r}"
        )
        clause = text[text.index(marker):]
        clause = clause[:clause.index("This does not change the KEY")]
        # Whitespace-normalised because the list WRAPS: an arc and its example
        # id land on either side of a newline plus four spaces of markdown
        # indent, and a line-wise pattern reads five arcs out of six.
        stated = dict(re.findall(r"(\w+) ([A-Z]+)-\d+",
                                 re.sub(r"\s+", " ", clause)))
        assert stated == ident_prefix.ARC_PREFIXES, (
            "conventions.md rule 10 and ARC_PREFIXES disagree.\n"
            f"  rule 10: {dict(sorted(stated.items()))}\n"
            f"  gate:    {dict(sorted(ident_prefix.ARC_PREFIXES.items()))}"
        )

    def test_every_arc_in_the_corpus_holds_a_prefix(self):
        """A seventh arc added to the registries and not here mints nothing.

        Asked against ``ARC_DOCS``, this package's own list of arcs, rather
        than a second hand-spelled list -- which is the defect this whole
        change is about.
        """
        assert set(ident_prefix.ARC_PREFIXES) == set(registry.ARC_DOCS), (
            f"prefixes: {sorted(ident_prefix.ARC_PREFIXES)}, "
            f"arcs: {sorted(registry.ARC_DOCS)}"
        )

    def test_no_two_arcs_share_a_prefix(self):
        """A shared prefix makes the arm unable to decide, and it fails SILENTLY.

        :func:`arc_of_prefix` inverts the map, so a duplicate simply loses one
        arc and every row of the loser reads as a trespass -- or, worse, the
        winner's rows read as clean.
        """
        prefixes = list(ident_prefix.ARC_PREFIXES.values())
        assert len(prefixes) == len(set(prefixes)), (
            f"two arcs share a prefix: {sorted(ident_prefix.ARC_PREFIXES.items())}"
        )

    def test_no_arc_prefix_collides_with_a_family_that_predates_the_ruling(self):
        """An arc minting ``N-`` would be carved out of its own rule.

        The carve-out is checked BEFORE the arc lookup, so a prefix in both
        sets is never graded at all -- silence that looks exactly like
        compliance.
        """
        clash = set(ident_prefix.ARC_PREFIXES.values()) & \
            ident_prefix.LEGACY_HYPHENATED_FAMILIES
        assert not clash, (
            f"{sorted(clash)} is both an arc's prefix and a legacy family, so "
            "rows carrying it are carved out of the rule that governs them"
        )

    def test_the_legacy_families_are_a_census_not_a_subtraction(self):
        """Every hyphenated family in the corpus is either legacy or an arc's.

        The set is ENUMERATED rather than spelled "everything except an arc
        prefix", because a set defined by subtraction claims members nobody
        censused.  This arm is what keeps the enumeration honest: a family in
        the corpus and in neither set would otherwise be reported as a typo.
        """
        known = set(ident_prefix.ARC_PREFIXES.values()) | \
            ident_prefix.LEGACY_HYPHENATED_FAMILIES
        found = {
            m["prefix"]: row.key for row in registry.ledger_rows()
            if (m := ident_prefix.PREFIXED_ID_RX.match(row.bare_ident))
        }
        unknown = {p: k for p, k in found.items() if p not in known}
        assert not unknown, (
            f"hyphenated families in the corpus that the census does not name: "
            f"{unknown}. Add them to LEGACY_HYPHENATED_FAMILIES or fix the row"
        )

    def test_the_arm_fires_on_an_id_carrying_another_arcs_prefix(self, stage):
        """A control nobody has seen fail is a number, not a gate.

        Planted on a REAL row, so it exercises the same seven-cell parser the
        live file uses rather than a table the control wrote itself.
        """
        prefix = a_live_ledger_row()
        arc = _arc_of(prefix)
        other, foreign = self._a_prefix_other_than(arc)
        ident = f"{foreign}-999"
        row = row_of("ledger", prefix)
        stage("ledger", row, with_cell(row, 1, ident))

        staged = _staged(ident_prefix.ledger_id_prefix_violations(), arc, ident)
        assert len(staged) == 1, staged
        assert "rule 10" in staged[0] and f"is {other}'s" in staged[0]

    def test_the_arm_fires_on_an_unrecognised_family(self, stage):
        """A typo of an arc's own prefix, which the carve-out must not swallow.

        ``BLA-462`` for ``BAL-462`` is the realistic instance: were the
        predicate written as "grade anything that is not a legacy family", the
        typo would read as a seventh family and pass.
        """
        prefix = a_live_ledger_row()
        arc = _arc_of(prefix)
        typo = ident_prefix.ARC_PREFIXES[arc][::-1]
        assert typo not in ident_prefix.ARC_PREFIXES.values(), typo
        ident = f"{typo}-462"
        row = row_of("ledger", prefix)
        stage("ledger", row, with_cell(row, 1, ident))

        staged = _staged(ident_prefix.ledger_id_prefix_violations(), arc, ident)
        assert len(staged) == 1, staged
        assert "neither an arc's prefix" in staged[0]

    def test_the_arm_is_silent_on_a_family_that_predates_the_ruling(self, stage):
        """The carve-out, mutated in its OWN direction.

        Rule 10 forbids renaming what is already filed, so an ``N-`` id in any
        arc must report NOTHING while the two arms above report their staged
        row.  Deleting the carve-out fails THIS arm and not those, which is
        what makes the directions independent.
        """
        prefix = a_live_ledger_row()
        row = row_of("ledger", prefix)
        stage("ledger", row, with_cell(row, 1, "N-99999"))

        assert not ident_prefix.ledger_id_prefix_violations()

    def test_a_suffixed_id_is_graded_rather_than_skipped(self, stage):
        """The hole a surviving mutation named, closed in the PREDICATE.

        ``BI-477a`` filed in ``balance`` is wrong for exactly the reason
        ``BI-477`` is, and under the first draft's ``fullmatch`` pattern over
        ``<UPPERCASE>-<digits>`` it was silent -- which is why loosening that
        pattern to ``search`` killed no control at all.  An unkilled mutation
        is a claim about the CODE before it is a claim about the tests.
        """
        prefix = a_live_ledger_row()
        arc = _arc_of(prefix)
        _, foreign = self._a_prefix_other_than(arc)
        ident = f"{foreign}-462a"
        row = row_of("ledger", prefix)
        stage("ledger", row, with_cell(row, 1, ident))

        staged = _staged(ident_prefix.ledger_id_prefix_violations(), arc, ident)
        assert len(staged) == 1, staged

    def test_the_arm_is_silent_on_an_id_carrying_no_hyphen(self, stage):
        """``P76``, ``D52``, ``E2`` and ``X5`` never reach the pattern at all.

        A second silent direction, and a different mechanism from the one
        above: these are carved out by ``fullmatch`` rather than by the family
        set, so a pattern loosened to ``search`` would start grading them.
        """
        prefix = a_live_ledger_row()
        row = row_of("ledger", prefix)
        stage("ledger", row, with_cell(row, 1, "P999"))

        assert not ident_prefix.ledger_id_prefix_violations()

    def test_the_arm_is_silent_on_an_id_that_matches_its_own_arc(self, stage):
        """The whole point, staged: a correct new id passes.

        Without this, every firing arm above is equally consistent with an arm
        that reports EVERY prefixed id, which would refuse the first correct
        one anybody mints.
        """
        prefix = a_live_ledger_row()
        arc = _arc_of(prefix)
        ident = f"{ident_prefix.ARC_PREFIXES[arc]}-462"
        row = row_of("ledger", prefix)
        stage("ledger", row, with_cell(row, 1, ident))

        assert not ident_prefix.ledger_id_prefix_violations()
