"""The duplicate-code arm (the coordinator's correction to Fork 5 item 4): a pair between
parts is accepted only when pylint's own Symilar confirms it was already in the source, and
refused when it touches a generated line or when the move altered a side."""
from __future__ import annotations

from pathlib import Path

from tools.split._render import importer_rewrites, line_limit, render_all, write
from tools.split._similar import pairs, sort_pairs
from tools.split._source import collect_rules
from tools.split.conftest import SAMPLE, build, layout_of, plan_dict

PACKAGE = "tests/test_area/test_sample"

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
    write(layout, root, render_all(layout, line_limit(root)), importer_rewrites(layout, root))
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
    assert "pylint does not report the source ranges" in verdict.created[0][2]


def test_a_duplicate_in_generated_lines_is_created(tmp_path):
    """A pair over two parts' docstrings and imports touches lines the tool generated."""
    root, layout = _split(tmp_path, second_floor=9)
    messages = _message(f"=={PACKAGE.replace('/', '.')}.test_big_early:[0:4]",
                        f"=={PACKAGE.replace('/', '.')}.test_big_late:[0:4]")
    verdict = _sorted(root, layout, messages)
    assert not verdict.revealed
    assert "touches a generated line" in verdict.created[0][2]
