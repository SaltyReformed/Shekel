"""The ONE reader (:mod:`tools.split._source`): every text, process output, JSON value, syntax
tree, symbol table and ini file the tool reads goes through it, and each refuses, naming what
it read, where Python would raise (the coordinator's ruling on the delta review's L-B,
2026-10-15: every path from ``split.main`` to a traceback becomes a refusal naming the file).
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

from tools.split import _dynamic as dynamic
from tools.split import _run as run
from tools.split._model import load_plan
from tools.split._render import importer_rewrites, line_limit
from tools.split._similar import checker_defaults
from tools.split._source import (
    Refusal,
    decode,
    load_json,
    parse,
    read_ini,
    read_text,
    symbols,
    text_holding,
)
from tools.split.conftest import SOURCE, build, layout_of, refusal, sample_with
from tools.split.test_apply import applied, proof
from tools.split.test_split import only_on_path

#: The tool's modules, every one but the reader itself and the tests.
TOOL = sorted(p for p in Path(__file__).parent.glob("*.py")
              if not p.stem.startswith("test_") and p.stem not in {"conftest", "_source"})

#: Calls that read, decode, parse or load text, by the attribute they call on a module name
#: (``json.loads``) or on any object (``.read_text``, ``.decode``), or by the bare name.
#: ``astroid.parse`` is not one: :mod:`tools.split._similar` gives pylint's own parser text
#: that :func:`tools.split._source.read_module` has already read and compiled.
_ON_MODULE = {"json": {"load", "loads"}, "ast": {"parse"}, "symtable": {"symtable"},
              "tokenize": None, "configparser": None, "io": {"open", "TextIOWrapper"}}
_ON_ANY = {"read_text", "decode", "open", "readlines"}
_BARE = {"open", "compile"}
#: A subprocess keyword that decodes the process's output.
_DECODING = {"text", "encoding", "errors", "universal_newlines"}


def readers(path: Path) -> list[str]:
    """Each call in ``path`` that reads, decodes, parses or loads text itself, as
    ``<line>: <call>``."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ast.unparse(func)
        if isinstance(func, ast.Name) and func.id in _BARE:
            found.append(f"{node.lineno}: {name}")
        elif isinstance(func, ast.Attribute):
            owner = func.value.id if isinstance(func.value, ast.Name) else None
            if owner in _ON_MODULE and (_ON_MODULE[owner] is None
                                        or func.attr in _ON_MODULE[owner]):
                found.append(f"{node.lineno}: {name}")
            elif func.attr in _ON_ANY:
                found.append(f"{node.lineno}: {name}")
            elif owner == "subprocess" and {k.arg for k in node.keywords} & _DECODING:
                found.append(f"{node.lineno}: {name} decoding its output")
    return found


def test_no_module_but_the_reader_reads_text_itself():
    """Every other module reads through :mod:`tools.split._source`, so a decode, JSON, parse
    or ini error is a refusal naming the file wherever it is read."""
    assert {str(p.name): readers(p) for p in TOOL if readers(p)} == {}


def test_the_census_sees_every_spelling_it_refuses():
    """The control: the scan finds each kind of read in the reader itself, so an empty
    answer above is the modules' and not the scan's blindness."""
    found = " ".join(readers(Path(__file__).parent / "_source.py"))
    for call in ("data.decode", "json.loads", "compile", "ast.parse", "symtable.symtable",
                 "configparser.ConfigParser", "tokenize.generate_tokens"):
        assert call in found, call
    assert {"split.py", "_run.py", "_render.py", "_prove.py", "_similar.py"} <= {
        p.name for p in TOOL}


class TestTheReader:
    """Each function, refusing as its docstring says."""

    def test_decode_names_the_file_the_byte_and_its_line(self):
        """The refusal shows the record holding the byte, a line of a file by default."""
        with pytest.raises(Refusal, match=re.escape(
                "f.py: not UTF-8 text (invalid continuation byte at byte 11, "
                "in b'b = \"\\xe9\"')")):
            decode(b'a = 1\nb = "\xe9"\nc = 2\n', "f.py")

    def test_a_record_ends_at_its_separator(self):
        """``git ls-files -z`` ends a path at a NUL: the refusal shows that path alone."""
        with pytest.raises(Refusal, match=re.escape("in b'b\\xe9.py')")):
            decode(b"a.py\0b\xe9.py\0c.py\0", "git ls-files' output", b"\0")

    def test_read_text_translates_newlines_as_path_read_text_does(self, tmp_path):
        """A CRLF and a lone CR read as LF, so a reader moved here sees what it saw."""
        path = tmp_path / "f.txt"
        path.write_bytes(b"a\r\nb\rc\n")
        assert read_text(path) == "a\nb\nc\n" == path.read_text(encoding="utf-8")

    def test_text_holding_decodes_only_a_file_that_names_the_needle(self, tmp_path):
        """A file not naming it is never decoded, so its bytes cannot refuse it; one that
        names it is decoded untranslated, its carriage returns kept."""
        stray, named = tmp_path / "stray.py", tmp_path / "named.py"
        stray.write_bytes(b"x = '\xe9'\n")
        named.write_bytes(b"import test_sample\r\n")
        assert text_holding(stray, "test_sample") is None
        assert text_holding(named, "test_sample") == "import test_sample\r\n"
        named.write_bytes(b"import test_sample  # \xe9\n")
        with pytest.raises(Refusal, match="named.py: not UTF-8 text"):
            text_holding(named, "test_sample")

    def test_load_json_names_its_source(self):
        """A value that is not JSON is refused, naming where it came from."""
        with pytest.raises(Refusal, match="pylint's output: not JSON"):
            load_json("[{", "pylint's output")

    @pytest.mark.parametrize("text, why", [
        ("def f(:\n", "invalid syntax"),
        ("def test_x():\n    break\n", "'break' outside loop"),
        ("x = '\0'\n", "source code string cannot contain null bytes"),
    ], ids=["a-syntax-error", "an-error-only-the-compiler-finds", "a-null-byte"])
    def test_parse_refuses_what_python_does_not_compile(self, text, why):
        """``ast.parse`` accepts a ``break`` outside a loop; Python does not compile it."""
        with pytest.raises(Refusal, match=re.escape(f"m.py: Python does not compile it ({why}")):
            parse(text, "m.py")

    def test_symbols_refuses_a_symbol_error(self):
        """A name both a parameter and global is refused, not raised."""
        with pytest.raises(Refusal, match="m.py: Python does not compile it"):
            symbols("def f(x):\n    global x\n", "m.py")

    @pytest.mark.parametrize("data, why", [
        (None, "no {path} to read"),
        (b"[FORMAT]\nmax-line-length=99 \xe9\n", "{path}: not an ini file configparser reads"),
        (b"max-line-length=99\n", "{path}: not an ini file configparser reads"),
    ], ids=["absent", "not-utf8", "no-section-header"])
    def test_read_ini(self, tmp_path, data, why):
        """Absent, not UTF-8, or not an ini file: each refused, naming the file."""
        path = tmp_path / ".pylintrc"
        if data is not None:
            path.write_bytes(data)
        with pytest.raises(Refusal, match=re.escape(why.format(path=path))):
            read_ini(path)


class TestEachSiteRefusesNamingTheFile:
    """The sites where a reader's refusal is the message: each once raised (the delta
    review's L-B, measured or reasoned there)."""

    def test_a_source_python_does_not_compile_is_refused_at_read(self, tmp_path):
        """Not raised later inside the layout, or carried to the record run."""
        assert refusal(tmp_path, sample_with('        """One."""\n',
                                             '        """One."""\n        break\n')) \
            .endswith("Python does not compile it ('break' outside loop, line 40)")

    def test_a_stray_file_not_naming_the_source_is_never_decoded(self, repo):
        """The importer walk checks the stem at the bytes level before decoding, so a
        non-UTF-8 ``.py`` anywhere that does not name it is no refusal; one that does is
        refused by name; and the prover's walk after the split reads alike."""
        (repo / "tests/test_stray.py").write_bytes(b"x = '\xe9'\n")
        assert [r.path.name for r in importer_rewrites(layout_of(repo), repo)] == [
            "test_other.py"]
        layout = applied(repo)
        assert not proof(repo, layout)
        (repo / "tests/test_named.py").write_bytes(b"# test_sample \xe9\n")
        assert any(f"importers: {repo / 'tests/test_named.py'}: not UTF-8 text" in failure
                   for failure in proof(repo, layout))

    def test_a_package_file_the_prover_cannot_decode_is_a_failure(self, repo):
        """The importer walk skips the package, so the prover's own walk for imports of
        ``_helpers`` is the one reading a part: a part naming it in bytes not UTF-8 is a
        failure of the static proof, not a refusal escaping it after apply has written."""
        layout = applied(repo)
        helpers = repo / "tests/test_area/test_sample/_helpers.py"
        helpers.write_bytes(helpers.read_bytes()
                            + b"# tests.test_area.test_sample._helpers \xe9\n")
        assert f"importers: {helpers}: not UTF-8 text" in " ".join(proof(repo, layout))

    def test_a_matched_document_not_in_utf8_is_refused(self, tmp_path):
        """classify reads a matched document whole to judge its first line (L-A item 4)."""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs/notes.md").write_bytes(b"see test_sample\n\xe9\n")
        with pytest.raises(Refusal, match=re.escape(f"{tmp_path / 'docs/notes.md'}: not UTF-8")):
            dynamic.classify(tmp_path, [("docs/notes.md", 1, "see test_sample")], {})

    @pytest.mark.parametrize("data, why", [(b"{\xe9}", "not UTF-8 text"),
                                           (b"{", "not JSON")], ids=["not-utf8", "not-json"])
    def test_a_plan_that_is_not_json_is_refused(self, tmp_path, data, why):
        """The plan, read before anything else, names itself."""
        plan = tmp_path / "x.plan.json"
        plan.write_bytes(data)
        with pytest.raises(Refusal, match=re.escape(f"{plan}: {why}")):
            load_plan(plan)

    def test_a_line_limit_that_is_not_a_number(self, tmp_path):
        """``max-line-length`` must be a whole number."""
        root = build(tmp_path)
        (root / ".pylintrc").write_text("[FORMAT]\nmax-line-length=wide\n", encoding="utf-8")
        with pytest.raises(Refusal, match="max-line-length is not a whole number"):
            line_limit(root)

    def test_the_similarity_defaults_need_the_config(self, tmp_path):
        """With no ``.pylintrc`` there is no config to check the defaults against."""
        with pytest.raises(Refusal, match="no .*\\.pylintrc to read"):
            checker_defaults(tmp_path)

    def test_pylint_output_that_is_not_json(self, repo, monkeypatch, tmp_path):
        """A pylint whose output is cut short: refused, not a JSONDecodeError."""
        only_on_path(tmp_path, monkeypatch)
        fake = tmp_path / "bin" / "pylint"
        fake.write_text("#!/bin/sh\nprintf '[{'\n", encoding="utf-8")
        fake.chmod(0o755)
        with pytest.raises(Refusal, match="pylint's output: not JSON"):
            run.pylint_messages(repo, [SOURCE])

    def test_a_path_git_lists_that_is_not_utf8(self, repo):
        """The walk's own output: the refusal shows the path."""
        (repo / os.fsdecode(b"tests/test_\xe9.py")).write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(Refusal, match=re.escape("git ls-files' output: not UTF-8 text")):
            run.python_files(repo)
