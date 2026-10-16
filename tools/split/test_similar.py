"""The duplicate-code arm (the coordinator's correction to Fork 5 item 4): a pair between
parts is accepted only when each side is one verbatim source run and pylint's own Symilar
confirms the pair was already in the source; refused when it touches a generated line, when
the move altered a side, or when a side joins blocks the source did not hold together."""
from __future__ import annotations

from pathlib import Path

from tools.split._render import importer_rewrites, line_limit, render_all
from tools.split._run import pylint_messages
from tools.split._similar import pairs, sort_pairs
from tools.split._source import collect_rules
from tools.split.conftest import PACKAGE, SAMPLE, build, layout_of, plan_dict, written

#: A body long enough for pylint's four-line minimum, written twice in the source.
BODY = ("    total = 0\n    for value in (1, 2, 3, 4):\n        total += value\n"
        "    assert total == 10\n    assert total > {floor}\n")


def _source(second_floor: int) -> str:
    """The sample with two module-level tests, whose bodies match when the floors do."""
    first = 'def test_sum_a():\n    """A."""\n' + BODY.format(floor=9)
    second = 'def test_sum_b():\n    """B."""\n' + BODY.format(floor=second_floor)
    return SAMPLE + "\n\n" + first + "\n\n" + second


def _split(tmp_path: Path, second_floor: int):
    """Split that source with test_sum_a in test_first and test_sum_b in test_big_late."""
    root = build(tmp_path, _source(second_floor))
    plan = plan_dict()
    plan["parts"][0]["nodes"].append("test_sum_a")
    plan["parts"][2]["nodes"].append("test_sum_b")
    layout = layout_of(root, plan)
    written(layout, root, render_all(layout, line_limit(root)),
            importer_rewrites(layout, root), {})
    return root, layout


def _range(root: Path, module: str, first_line: str, count: int) -> str:
    """``==<dotted>:[start:end]`` for ``count`` lines starting at ``first_line``."""
    lines = (root / PACKAGE / f"{module}.py").read_text(encoding="utf-8").splitlines()
    start = lines.index(first_line)
    return f"=={PACKAGE.replace('/', '.')}.{module}:[{start}:{start + count}]"


def _message(*sides: str) -> list[dict]:
    """A pylint duplicate-code message naming ``sides``, as its JSON output carries it."""
    return [{"symbol": "duplicate-code",
             "message": "Similar lines in 2 files\n" + "\n".join(sides) + "\n  code"}]


def _sorted(root: Path, layout, messages: list[dict]):
    """The arm's verdict on ``messages`` over the split under ``root``."""
    source = "\n".join(layout.source.lines) + "\n"
    return sort_pairs(layout, root, collect_rules(root), messages, source)


def _reported(root: Path) -> list[dict]:
    """The duplicate-code messages pylint itself reports over the package's files."""
    files = sorted(str(p.relative_to(root)) for p in (root / PACKAGE).glob("*.py"))
    return [m for m in pylint_messages(root, files) if m["symbol"] == "duplicate-code"]


def _sides(messages: list[dict]) -> list[list[str]]:
    """Each reported pair as its two ``==<dotted>:[start:end]`` sides, sorted."""
    return [sorted(f"=={s.path[:-3].replace('/', '.')}:[{s.start}:{s.end}]" for s in pair)
            for pair in pairs(messages)]


def test_the_message_names_each_pair():
    """A message over three files is three pairs."""
    three = [{"symbol": "duplicate-code", "message": "x\n==a.b:[1:5]\n==a.c:[2:6]\n==a.d:[3:7]"}]
    assert len(pairs(three)) == 3


def test_a_genuine_pre_existing_pair_is_revealed(tmp_path):
    """Two identical bodies in the source, now in two parts: pylint confirms the source pair."""
    root, layout = _split(tmp_path, second_floor=9)
    messages = _message(_range(root, "test_first", "    total = 0", 5),
                        _range(root, "test_big_late", "    total = 0", 5))
    verdict = _sorted(root, layout, messages)
    assert len(verdict.revealed) == 1 and not verdict.created


def test_a_side_the_move_altered_is_created(tmp_path):
    """The source bodies differ; a part rewritten to match the other is not a source pair."""
    root, layout = _split(tmp_path, second_floor=8)
    late = root / PACKAGE / "test_big_late.py"
    late.write_text(late.read_text(encoding="utf-8").replace(
        "assert total > 8", "assert total > 9"), encoding="utf-8")
    messages = _message(_range(root, "test_first", "    total = 0", 5),
                        _range(root, "test_big_late", "    total = 0", 5))
    verdict = _sorted(root, layout, messages)
    assert not verdict.revealed
    assert "text differs from the source's" in verdict.created[0][2]


def test_two_verbatim_runs_pylint_does_not_pair_are_created(tmp_path):
    """Both sides are verbatim source runs, but their source bodies differ in the last line:
    the arm asks pylint's Symilar of the SOURCE ranges, never trusts the message's pairing."""
    root, layout = _split(tmp_path, second_floor=8)
    messages = _message(_range(root, "test_first", "    total = 0", 5),
                        _range(root, "test_big_late", "    total = 0", 5))
    verdict = _sorted(root, layout, messages)
    assert not verdict.revealed
    assert "pylint does not report the source ranges" in verdict.created[0][2]


def test_a_range_paired_with_itself_is_created(tmp_path):
    """Two sides that map to one source range are not two places the source held a body."""
    root, layout = _split(tmp_path, second_floor=9)
    side = _range(root, "test_first", "    total = 0", 5)
    verdict = _sorted(root, layout, _message(side, side))
    assert not verdict.revealed
    assert "map to one source range" in verdict.created[0][2]


# Two tests of one class, adjacent in the source, whose run from the first's body into the
# second's repeats in a second adjacent pair: the shape test_salary's split revealed
# (TestDeductionCadenceForm, its members 6-7 and 19-20).  Each pair sits mid-slice, after a
# test of its own, as there: a pair opening its slice would extend pylint's run up onto the
# slice's generated ``class`` line, which is identical in both parts and refused.
TWIN = '''

class TestTwin:
    """A class split so that each slice holds two adjacent tests after one of its own."""

    def test_o(self):
        """O."""
        assert "o".upper() == "O"

    def test_p(self):
        """P."""
        total = 0
        for value in (1, 2, 3, 4):
            total += value
        assert total == 10

    def test_q(self):
        """Q."""
        assert sum((1, 2, 3, 4)) == 10
        assert max((1, 2, 3, 4)) == 4

    def test_n(self):
        """N."""
        assert len([None] * 3) == 3

    def test_r(self):
        """R."""
        total = 0
        for value in (1, 2, 3, 4):
            total += value
        assert total == 10

    def test_s(self):
        """S."""
        assert sum((1, 2, 3, 4)) == 10
        assert max((1, 2, 3, 4)) == 4
'''


def _with(tmp_path: Path, extra: str, placed: dict[int, list]):
    """Split the sample plus ``extra``, with ``placed[i]``'s nodes added to part ``i``."""
    root = build(tmp_path, SAMPLE + extra)
    plan = plan_dict()
    for part, nodes in placed.items():
        plan["parts"][part]["nodes"].extend(nodes)
    layout = layout_of(root, plan)
    written(layout, root, render_all(layout, line_limit(root)),
            importer_rewrites(layout, root), {})
    return root, layout


def test_a_run_across_two_members_adjacent_in_the_source_is_revealed(tmp_path):
    """Each side crosses from one test of a split class into the next, as the two were in
    the source; it is one verbatim source run, and pylint pairs the source ranges.  The pair
    is the one pylint itself reports over the parts, not a range built by hand."""
    root, layout = _with(tmp_path, TWIN, {
        0: [{"class": "TestTwin", "tests": ["test_o", "test_p", "test_q"],
             "docstring": "o, p and q."}],
        2: [{"class": "TestTwin", "tests": ["test_n", "test_r", "test_s"],
             "docstring": "n, r and s."}]})
    messages = _reported(root)
    assert _sides(messages) == [sorted([_range(root, "test_first", "        total = 0", 9),
                                        _range(root, "test_big_late", "        total = 0", 9)])]
    verdict = _sorted(root, layout, messages)
    assert not verdict.created
    assert len(verdict.revealed) == 1


# test_x and test_y meet in a part only because test_mid, between them in the source, went
# elsewhere; their copies test_x2 and test_y2 were adjacent in the source.
APART = '''

def test_x():
    """X."""
    left = [1, 2]
    right = [3, 4]
    assert left + right == [1, 2, 3, 4]


def test_mid():
    """Mid."""
    assert len("mid") == 3


def test_y():
    """Y."""
    items = sorted([4, 3])
    assert items == [3, 4]


def test_x2():
    """X2."""
    left = [1, 2]
    right = [3, 4]
    assert left + right == [1, 2, 3, 4]


def test_y2():
    """Y2."""
    items = sorted([4, 3])
    assert items == [3, 4]
'''


#: APART with test_mid gone and test_x three blank lines above test_y: adjacent in the
#: source, but the renderer sets two blank lines between top-level blocks.
GAP = APART.replace('''    assert left + right == [1, 2, 3, 4]


def test_mid():
    """Mid."""
    assert len("mid") == 3


def test_y''', '''    assert left + right == [1, 2, 3, 4]



def test_y''')


def test_a_run_across_blocks_apart_in_the_source_is_created(tmp_path):
    """Two blocks adjacent in a part but not in the source: the run pylint reports joining
    them is duplication the split created."""
    root, layout = _with(tmp_path, APART, {0: ["test_x", "test_y"], 1: ["test_mid"],
                                           2: ["test_x2", "test_y2"]})
    messages = _reported(root)
    assert _sides(messages) == [sorted([_range(root, "test_first", "    left = [1, 2]", 9),
                                        _range(root, "test_big_late", "    left = [1, 2]", 9)])]
    verdict = _sorted(root, layout, messages)
    assert not verdict.revealed
    assert "not one source run" in verdict.created[0][2]


def test_a_run_across_a_gap_the_renderer_changed_is_created(tmp_path):
    """Two blocks adjacent in the source, three blank lines apart, rendered two apart: the
    run pylint reports is not the source's text, so it is refused rather than mapped."""
    assert "test_mid" not in GAP and "]\n\n\n\ndef test_y():" in GAP
    root, layout = _with(tmp_path, GAP, {0: ["test_x", "test_y"], 2: ["test_x2", "test_y2"]})
    messages = _reported(root)
    assert _sides(messages) == [sorted([_range(root, "test_first", "    left = [1, 2]", 9),
                                        _range(root, "test_big_late", "    left = [1, 2]", 9)])]
    verdict = _sorted(root, layout, messages)
    assert not verdict.revealed
    assert "not one source run" in verdict.created[0][2]


# test_k's body repeats as test_k2's.  In the part, test_k is followed by test_z, whose
# leading banner comment test_mid2 kept apart from it in the source: the shape test_loan's
# re-proof measured (its "# -- Nav-Pills Consistency Tests --" banner).
TRAILING = '''

def test_k():
    """K."""
    left = [1, 2]
    right = [3, 4]
    assert left + right == [1, 2, 3, 4]
    assert len(left) == 2
    assert len(right) == 2


def test_mid2():
    """Mid."""
    assert "m" * 2 == "mm"


# -- A banner ----------------------------------------------------------
def test_z():
    """Z."""
    assert "z".upper() == "Z"


def test_k2():
    """K2."""
    left = [1, 2]
    right = [3, 4]
    assert left + right == [1, 2, 3, 4]
    assert len(left) == 2
    assert len(right) == 2


def test_w():
    """W."""
    assert "w".upper() == "W"
'''


def test_a_trailing_comment_of_a_block_apart_in_the_source_is_revealed(tmp_path):
    """pylint's reported range runs past the last line it compares, onto test_z's banner,
    which leads a block test_k was not adjacent to in the source.  The compared lines are one
    verbatim source run, so the pair is revealed; the banner is a moved line, so (1) holds."""
    root, layout = _with(tmp_path, TRAILING, {0: ["test_k", "test_z"], 1: ["test_mid2"],
                                              2: ["test_k2", "test_w"]})
    messages = _reported(root)
    first = (root / PACKAGE / "test_first.py").read_text(encoding="utf-8").splitlines()
    banner = next(i for i, line in enumerate(first) if line.startswith("# -- A banner"))
    side = next(s for pair in pairs(messages) for s in pair if s.path.endswith("test_first.py"))
    assert len(pairs(messages)) == 1 and side.start < banner < side.end
    verdict = _sorted(root, layout, messages)
    assert not verdict.created
    assert len(verdict.revealed) == 1


def test_a_duplicate_in_generated_lines_is_created(tmp_path):
    """A pair over two parts' docstrings and imports touches lines the tool generated."""
    root, layout = _split(tmp_path, second_floor=9)
    messages = _message(f"=={PACKAGE.replace('/', '.')}.test_big_early:[0:4]",
                        f"=={PACKAGE.replace('/', '.')}.test_big_late:[0:4]")
    verdict = _sorted(root, layout, messages)
    assert not verdict.revealed
    assert "touches a generated line" in verdict.created[0][2]
