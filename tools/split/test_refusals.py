"""Every refusal the rulings name, each shown REFUSING a source or plan built to need it:
the tool never guesses, so a split it cannot prove is not made."""
from __future__ import annotations

import hashlib
import json
import textwrap

import pytest

from tools.split._model import PlanError, load_plan
from tools.split._render import line_limit, render_all
from tools.split.conftest import (
    SAMPLE,
    build,
    layout_of,
    plan_dict,
    plan_of,
    refusal,
    sample_with,
)


#: A module-level node appended to the sample, placed (when it is a test) in test_first.
EXTRA_TOP = "\n\n{body}\n"


def appended(body: str) -> str:
    """The sample with ``body`` appended as a new top-level node."""
    return SAMPLE + EXTRA_TOP.format(body=body)


def request_reads_a_class_docstring() -> str:
    """The sample with its autouse fixture reading the requesting test's class docstring
    through ``request``."""
    return sample_with('monkeypatch.setenv("SAMPLE", "1")',
                       'monkeypatch.setenv("SAMPLE", str(request.cls.__doc__))').replace(
        "def _frozen(monkeypatch):", "def _frozen(monkeypatch, request):")


def with_test_first(*names: str) -> dict:
    """The sample plan with ``names`` also placed in test_first."""
    plan = plan_dict()
    plan["parts"][0]["nodes"].extend(names)
    return plan


class TestTheSourceReaderRefuses:
    """What no move can keep, refused when the source is read."""

    def test_a_comment_in_the_import_block(self, tmp_path):
        """It has no node to travel with."""
        source = sample_with("import re\n", "import re  # regex\n")
        assert "in the import block would be lost" in refusal(tmp_path, source)

    def test_an_import_after_the_first_node(self, tmp_path):
        """A late import's position is part of its meaning."""
        assert "an import after the first non-import" in refusal(
            tmp_path, appended("import os"))

    def test_a_relative_import(self, tmp_path):
        """A part sits one directory deeper, so the dots would mean something else."""
        source = sample_with("from tests.shared import", "from ..shared import")
        assert "relative or star import" in refusal(tmp_path, source)

    def test_a_module_level_statement_with_no_name(self, tmp_path):
        """A bare call runs at import; where it runs is part of what it does."""
        assert "has no single name to move by" in refusal(tmp_path, appended("print('x')"))

    def test_a_name_bound_twice(self, tmp_path):
        """Which binding a part sees would depend on where each went."""
        assert "names bound twice" in refusal(tmp_path, appended("_START = 1"))

    def test_lines_after_the_last_node(self, tmp_path):
        """A trailing comment has no node to travel with."""
        assert "lines after the last node" in refusal(tmp_path, SAMPLE + "\n# trailing\n")


class TestTheLayoutRefuses:
    """What the plan asks that the proof could not show is pure."""

    def test_an_autouse_fixture_placed_in_a_part(self, tmp_path):
        """Fixtures go to conftest.py, where they reach exactly the moved tests."""
        assert "fixture _frozen belongs in conftest.py" in refusal(
            tmp_path, plan=with_test_first("_frozen"))

    def test_a_module_scoped_fixture(self, tmp_path):
        """In a package conftest it would run once per part."""
        source = sample_with("@pytest.fixture(autouse=True)",
                             '@pytest.fixture(autouse=True, scope="module")')
        assert "scope 'module' would run once per part" in refusal(tmp_path, source)

    def test_a_file_relative_path(self, tmp_path):
        """__file__ means a directory one level up once the test is in a part."""
        source = appended('def test_path():\n    """P."""\n    assert __file__')
        message = refusal(tmp_path, source, with_test_first("test_path"))
        assert "reads __file__, which names its own module" in message

    def test_a_module_name_lookup(self, tmp_path):
        """__name__ names a different module in a part."""
        source = appended('def test_name():\n    """N."""\n    assert __name__')
        assert "reads __name__" in refusal(tmp_path, source, with_test_first("test_name"))

    def test_a_hook_function(self, tmp_path):
        """Moved into a conftest a hook would reach every test in the package and more."""
        source = appended('def pytest_collection_modifyitems(items):\n    """R."""\n'
                          '    items.reverse()')
        assert "pytest reads it off the module" in refusal(tmp_path, source)

    def test_a_module_level_pytestmark(self, tmp_path):
        """A part would not carry it."""
        source = appended("pytestmark = pytest.mark.slow")
        assert "pytest reads it off the module" in refusal(tmp_path, source)

    def test_a_global_statement(self, tmp_path):
        """It writes the module it runs in."""
        source = appended('def test_global():\n    """G."""\n    global _CASES\n'
                          '    _CASES = []')
        assert "global _CASES" in refusal(tmp_path, source, with_test_first("test_global"))

    def test_an_unplaced_import(self, tmp_path):
        """Dropping an import is its finding's act, never a split's."""
        plan = plan_dict()
        del plan["unused_imports"]
        assert "imports nothing reads, placed nowhere: ['unused_thing']" in refusal(
            tmp_path, plan=plan)

    def test_a_placed_import_that_is_read(self, tmp_path):
        """A placement may only name an alias nothing reads."""
        plan = plan_dict()
        plan["unused_imports"]["helper_one"] = "test_first"
        assert "is not an unread alias" in refusal(tmp_path, plan=plan)

    def test_an_unplaced_test(self, tmp_path):
        """A test the plan forgets is a test the split would drop."""
        plan = plan_dict()
        plan["parts"][0]["nodes"].remove("test_top_level")
        assert "test test_top_level is placed in no part" in refusal(tmp_path, plan=plan)

    def test_a_name_placed_twice(self, tmp_path):
        """One node, one home."""
        plan = plan_dict()
        plan["parts"][1]["nodes"].append("test_top_level")
        assert "placed in both" in refusal(tmp_path, plan=plan)

    def test_a_part_reading_a_node_in_another_part(self, tmp_path):
        """Parts never import from each other; shared nodes belong in _helpers.py."""
        plan = with_test_first("_make")
        plan["parts"][1]["nodes"].append("test_top_level")
        plan["parts"][0]["nodes"].remove("test_top_level")
        source = sample_with("assert _START.year == 2026",
                             "assert _START.year == 2026 and _make(1)")
        assert "reads _make, placed in test_first" in refusal(tmp_path, source, plan)

    def test_a_split_globals_pair(self, tmp_path):
        """A globals() patch reaches only the module it runs in, so every reader of the key
        must share it."""
        source = appended(
            'def test_patches(monkeypatch):\n    """Patch."""\n'
            '    monkeypatch.setitem(globals(), "helper_one", lambda n: 0)\n'
            '    assert _make(5) == 5')
        message = refusal(tmp_path, source, with_test_first("test_patches"))
        assert "patches globals()['helper_one']" in message

    def test_a_transitive_reader_of_a_patched_key(self, tmp_path):
        """T -> _outer -> _inner reads the key: _inner, in _helpers, would miss the patch."""
        source = appended(
            'def _inner(n):\n    """Inner."""\n    return helper_one(n)\n\n\n'
            'def _outer(n):\n    """Outer."""\n    return _inner(n)\n\n\n'
            'def test_patches(monkeypatch):\n    """Patch."""\n'
            '    monkeypatch.setitem(globals(), "helper_one", lambda n: 0)\n'
            '    assert _outer(5) == 0')
        message = refusal(tmp_path, source, with_test_first("test_patches", "_outer"))
        assert "['_inner (_helpers)'] read within its reach" in message

    def test_a_reader_reached_only_as_a_callback_value(self, tmp_path):
        """A helper passed, not called, still runs under the patch."""
        source = appended(
            'def _cb(n):\n    """Callback."""\n    return helper_one(n)\n\n\n'
            'def test_patches(monkeypatch):\n    """Patch."""\n'
            '    monkeypatch.setitem(globals(), "helper_one", lambda n: 0)\n'
            '    assert list(map(_cb, [5])) == [0]')
        message = refusal(tmp_path, source, with_test_first("test_patches"))
        assert "['_cb (_helpers)'] read within its reach" in message

    def test_a_patch_held_in_a_fixture(self, tmp_path):
        """A fixture's patch is live for every test that requests it; reach cannot model it."""
        source = sample_with('monkeypatch.setenv("SAMPLE", "1")',
                             'monkeypatch.setitem(globals(), "helper_one", lambda n: 0)')
        assert "patches globals() outside a test function's body" in refusal(tmp_path, source)

    def test_a_globals_call_whose_key_cannot_be_read(self, tmp_path):
        """A computed key could be any name.  The dict itself is refused by the identity
        screen (rule 4: ``globals`` reads the namespace of the module it runs in), before the
        reach rule reads its keys."""
        source = appended('def test_any():\n    """Any."""\n    assert globals()')
        assert "globals reads, imports or compiles by a name or a namespace" in refusal(
            tmp_path, source, with_test_first("test_any"))

    def test_a_patched_globals_key_computed_at_run_time(self, tmp_path):
        """The reach rule's own shape with a key it cannot read: the screen lets the
        ``setitem(globals(), ...)`` shape through to the reach rule, which refuses it."""
        source = appended('def test_any(monkeypatch):\n    """Any."""\n'
                          '    name = "helper_" + "one"\n'
                          '    monkeypatch.setitem(globals(), name, lambda n: 0)')
        assert "a shape the tool cannot prove" in refusal(
            tmp_path, source, with_test_first("test_any"))

    def test_inspect_on_a_moved_node(self, tmp_path):
        """inspect reads the file a node now sits in."""
        source = sample_with("import re\n", "import inspect\nimport re\n") + EXTRA_TOP.format(
            body='def test_src():\n    """S."""\n    assert inspect.getsource(_make)')
        assert "inspect.getsource reaches inspect, which reads where code runs" in refusal(
            tmp_path, source, with_test_first("test_src"))

    def test_a_part_over_the_line_budget(self, tmp_path):
        """No part may reach 1,000 lines."""
        padding = "\n".join(f"    assert {i} == {i}" for i in range(1000))
        source = appended(f'def test_long():\n    """Long."""\n{padding}')
        assert "over the 1000-line budget" in refusal(
            tmp_path, source, with_test_first("test_long"))


class TestARunTimeReadOfTheModuleIsRefused:
    """What running code reads of the module it runs in, or of a split class's docstring:
    each names the part a test now sits in, or a slice (the review's F9)."""

    def test_a_request_module_read(self, tmp_path):
        """A fixture reading request.module reads a part, not the source (refused by the
        request allowlist since 3C-3b, which deleted the location set's reason)."""
        source = sample_with('monkeypatch.setenv("SAMPLE", "1")',
                             'monkeypatch.setenv("SAMPLE", request.module.__name__)').replace(
            "def _frozen(monkeypatch):", "def _frozen(monkeypatch, request):")
        assert ("request.module.__name__ reads the fixture request beyond what moved code "
                "may") in refusal(tmp_path, source)

    @pytest.mark.parametrize("read", [
        "self.__module__", "type(self).__module__", 'getattr(self, "__module__")',
        "super().__module__",
    ])
    def test_a_module_read_off_the_running_test(self, tmp_path, read):
        """The running test's module is the part it now sits in (the review's F9)."""
        source = sample_with('"""One."""\n', f'"""One."""\n        assert {read}\n')
        assert "__module__ may name or reach where the code runs" in refusal(tmp_path, source)

    def test_a_module_read_off_the_request(self, tmp_path):
        """``request.function.__module__`` names the part a test now sits in."""
        source = sample_with('monkeypatch.setenv("SAMPLE", "1")',
                             'monkeypatch.setenv("SAMPLE", request.function.__module__)'
                             ).replace("def _frozen(monkeypatch):",
                                       "def _frozen(monkeypatch, request):")
        assert "request.function.__module__ may name or reach where the code runs" in refusal(
            tmp_path, source)

    def test_a_class_docstring_read_through_the_request_when_a_class_is_split(self, tmp_path):
        """``request.cls.__doc__`` reads a slice's one-line docstring once TestBig is split
        (refused by the request allowlist since 3C-3b, whether or not a class is split)."""
        assert "request.cls.__doc__ reads the fixture request beyond what moved code may" in (
            refusal(tmp_path, request_reads_a_class_docstring()))

    @pytest.mark.parametrize("body, why", [
        ("s = self\n        assert s.__module__", "__module__ may name"),
        ("assert inspect.getfile(type(self))", "inspect.getfile reaches inspect"),
        ("assert inspect.stack()[0].filename", "inspect.stack reaches inspect"),
        ('assert sys._getframe().f_globals["__name__"]', "sys._getframe reaches sys._getframe"),
        ("assert _make.__code__.co_filename", "co_filename may name"),
        ('assert _make.__globals__["__name__"]', "__globals__ may name"),
        ("assert self.test_one.__code__", "__code__ may name"),
        ("import tests.test_area.test_sample as me\n        assert me.__file__",
         "__file__ may name"),
        ("from tests.test_area import test_sample\n        assert test_sample.__file__",
         "__file__ may name"),
    ], ids=["an-alias-of-self", "inspect-on-type-self", "inspect-stack", "getframe",
            "a-moved-nodes-code", "a-moved-nodes-globals", "a-methods-code",
            "the-source-imported-by-path", "the-source-imported-by-name"])
    def test_an_identity_read_off_anything_but_a_foreign_import(self, tmp_path, body, why):
        """Refused WHATEVER it is read off, unless the chain is rooted at a name an import
        binds to something other than the source or its package (fork M2, ruled B): a list of
        the names running code reaches itself through was a list of spellings."""
        source = sample_with("import re\n", "import inspect\nimport re\nimport sys\n")
        source = source.replace('"""One."""\n', f'"""One."""\n        {body}\n', 1)
        assert why in refusal(tmp_path, source)

    def test_getfixturevalue(self, tmp_path):
        """A fixture set up by name at run time may sit on a branch no run took."""
        source = appended('def test_dyn(request):\n    """D."""\n'
                          '    assert request.getfixturevalue("_frozen") is None')
        assert "getfixturevalue sets up a fixture by name" in refusal(
            tmp_path, source, with_test_first("test_dyn"))


@pytest.mark.parametrize("source", [
    sample_with('"""One."""\n', '"""One."""\n        assert date.__module__ == "datetime"\n'),
    sample_with('"""One."""\n', '"""One."""\n        assert re.__file__\n'),
    sample_with('"""One."""\n', '"""One."""\n        import json\n        assert json.__file__\n'),
    sample_with("import re\n", "import inspect\nimport re\n").replace(
        '"""One."""\n', '"""One."""\n        assert inspect.getsource(re)\n', 1),
    sample_with('"""One."""\n', '"""One."""\n        assert self.__doc__ == "First tests."\n'),
], ids=["an-imported-objects-module", "an-imported-modules-file",
         "a-locally-imported-file", "inspect-on-an-imported-module",
         "a-whole-class-docstring"])
def test_a_read_the_split_cannot_change_is_accepted(tmp_path, source):
    """An imported object's module, an imported module's file (imported at module level or
    inside the test), inspect on an imported module, and the docstring of a class moved
    WHOLE each read the same after the split, so none is refused.  (A read through
    ``request`` beyond its allowlist is refused whether or not a class is split:
    ``tools/split/test_identity.py``'s ``TestTheRequestHalf``.)"""
    root = build(tmp_path, source)
    assert render_all(layout_of(root), line_limit(root))


def test_a_patch_whose_readers_are_out_of_reach_is_accepted(tmp_path):
    """A reader the patching test cannot reach does not run while the patch is live, so it
    may sit in another part (the reach rule, not a guess: every reached reader is checked)."""
    source = appended(
        'def test_patches(monkeypatch):\n    """Patch."""\n'
        '    monkeypatch.setitem(globals(), "helper_one", lambda n: 0)\n'
        '    assert helper_one(5) == 0\n\n\n'
        'def test_reads():\n    """Reads the unpatched helper."""\n    assert helper_one(2) == 2')
    plan = with_test_first("test_patches")
    plan["parts"][2]["nodes"].append("test_reads")
    root = build(tmp_path, source)
    assert render_all(layout_of(root, plan), line_limit(root))


def _split_class_source(member: str) -> str:
    """The sample with ``member`` added to TestBig after its docstring."""
    return sample_with('    """The big class, which is split across two parts."""\n',
                       '    """The big class, which is split across two parts."""\n\n'
                       f"{member}\n")


#: The refusal a split-class member named with two leading underscores meets.
_UNDERSCORES = "a member named with two leading underscores"


class TestASplitClassIsRefused:
    """Fork 2's refusals: what a mixin or a per-part class could change."""

    @pytest.mark.parametrize("member, why", [
        ('    @pytest.fixture(scope="class")\n    def _once(self):\n        """O."""',
         "a fixture scoped beyond one test"),
        ('    def setup_class(self):\n        """S."""', "runs once per class"),
        ('    def _kind(self):\n        """K."""\n        return self.__class__',
         "reaches its own class"),
        ('    def _kind(self):\n        """K."""\n        return type(self)',
         "reaches its own class"),
        ('    def _up(self):\n        """U."""\n        return super()', "reaches its own class"),
        ('    def _named(self):\n        """N."""\n        return TestBig',
         "reaches its own class"),
        ('    limit = 3\n\n    def _bump(self):\n        """B."""\n        self.limit = 4',
         "assigns self.limit"),
        ('    @classmethod\n    def _set(cls):\n        """S."""\n        cls.flag = True',
         "assigns cls.flag"),
        ('    def _about(self):\n        """A."""\n        return self.__doc__',
         "reads its class's docstring"),
        ('    @classmethod\n    def _about(cls):\n        """A."""\n'
         '        return getattr(cls, "__doc__")', "reads its class's docstring"),
        ('    def _about(self):\n        """A."""\n        return self.test_a.__qualname__',
         "reads its class's docstring, name, namespace or bases"),
        ('    @classmethod\n    def _about(cls):\n        """A."""\n        return cls.__mro__',
         "reads its class's docstring, name, namespace or bases"),
        ('    def _about(self):\n        """A."""\n        return self.__static_attributes__',
         "reads its class's docstring, name, namespace or bases"),
    ])
    def test_a_member_that_changes_meaning(self, tmp_path, member, why):
        """Each would mean something different on a mixin or a per-part class (the last,
        ``__static_attributes__``, is delta review 2's Low-1 member)."""
        assert why in refusal(tmp_path, _split_class_source(member))

    @pytest.mark.parametrize("member, why", [
        ('    def _kind(me):\n        """K."""\n        return me.__doc__',
         "_kind takes its receiver as anything but self"),
        ('    def _kind(me):\n        """K."""\n        type(me).flag = 1',
         "_kind takes its receiver as anything but self"),
        ('    def _kind(me):\n        """K."""\n        handed = me._shared\n'
         "        return handed.__qualname__", "_kind takes its receiver as anything but self"),
        ('    def _kind(*args):\n        """K."""\n        return args[0].__doc__',
         "_kind takes its receiver as anything but self"),
        ('    @classmethod\n    def _kind(klass):\n        """K."""\n'
         "        return klass.__doc__", "_kind takes its receiver as anything but cls"),
        ('    @classmethod\n    def _notes(cls):\n        """N."""\n'
         "        return cls.__annotations__", "reads its class's docstring, name, namespace"),
        ('    @classmethod\n    def _notes(cls):\n        """N."""\n'
         "        return cls.__annotate__", "reads its class's docstring, name, namespace"),
        ('    def _kind(self):\n        """K."""\n        return __class__.__doc__',
         "reaches its own class (['__class__'])"),
        ('    def __init_subclass__(cls, **kw):\n        """S."""', _UNDERSCORES),
        ('    def __set_name__(self, owner, name):\n        """S."""', _UNDERSCORES),
        ('    def __class_getitem__(cls, item):\n        """S."""', _UNDERSCORES),
        ("    __slots__ = ()", _UNDERSCORES),
        ('    __qualname__ = "x"', _UNDERSCORES),
        ("    __x = 1", _UNDERSCORES),
        ('    def _kind(self):\n        """K."""\n        return self.__x',
         "['__x'] in the private form"),
        ('    def _kind(self, __limit=1):\n        """K."""\n        __seen = __limit\n'
         "        return __seen", "['__limit', '__seen'] in the private form"),
        ("    where = helper_one(1)", "a class attribute must be a constant"),
        ("    where = helper_one", "a class attribute must be a constant"),
        ("    where: int", "a class attribute must be a constant"),
        ("    rows = (n for n in range(3))", "a class attribute must be a constant"),
        ('    class Inner:\n        """I."""', "a nested class"),
        ('    @helper_one\n    def _kind(self):\n        """K."""',
         "_kind's decorator helper_one is not one a split class may hold"),
        ('    @helper_one.setter\n    def _kind(self, value):\n        """K."""',
         "_kind's decorator helper_one.setter is not one a split class may hold"),
    ], ids=["a-receiver-named-otherwise", "a-write-through-a-receiver-named-otherwise",
            "a-member-handed-out-through-a-receiver-named-otherwise", "a-receiver-in-args",
            "a-class-receiver-named-otherwise", "the-class-annotations",
            "the-class-annotation-function", "the-class-cell", "a-subclass-hook",
            "a-set-name-hook", "a-class-getitem-hook", "slots", "a-qualname",
            "a-private-member", "a-private-read", "a-private-parameter-and-local", "a-call",
            "a-name",
            "an-annotation-with-no-value", "a-generator", "a-nested-class",
            "a-decorator-of-the-module", "an-accessor-off-an-import"])
    def test_a_member_outside_the_member_grammar(self, tmp_path, member, why):
        """The member grammar (the coordinator's ruling on 3C-3's Fork 2 and delta review 2's
        High-1, 2026-10-16, and on 3C-3b's report): each shape hands the instance or the
        class to a name no rule keys on, hands a hook the mixin or a slice, puts a name
        Python reads at class creation on the mixin, spells a private name by the mixin's
        name on one side of the split and the class's on the other, or may hold a
        descriptor; each was ACCEPTED by f3690b221's tool."""
        assert why in refusal(tmp_path, _split_class_source(member))

    def test_a_keyword_name_is_not_in_the_private_form(self, tmp_path):
        """The compiler does not mangle a call's keyword name (``f(__kw=1)`` passes
        ``__kw``), so a member passing one is accepted: the rule is the compiler's."""
        member = '    def _kind(self):\n        """K."""\n        return dict(__kw=1)'
        root = build(tmp_path, _split_class_source(member))
        assert render_all(layout_of(root), line_limit(root))

    def test_a_decorator_spelled_as_an_allowed_one_but_bound_to_another(self, tmp_path):
        """A decorator resolves BY IMPORT: ``cached_property`` bound to a helper of the
        source's own imports is not :func:`functools.cached_property`."""
        member = '    @cached_property\n    def _kind(self):\n        """K."""'
        source = _split_class_source(member).replace(
            "import re\n", "import re\n\nfrom tests.shared import helper_two as cached_property\n",
            1)
        assert "_kind's decorator cached_property is not one a split class may hold" in (
            refusal(tmp_path, source))

    def test_what_the_member_grammar_admits_is_accepted(self, tmp_path):
        """Every value, decorator and receiver the grammar names, each in the shape a split
        class may hold it."""
        member = textwrap.indent(textwrap.dedent('''\
            LIMIT = 3
            NAMES = ("a", "b")
            TABLE = {"a": [helper_one(1)]}
            LABEL = f"{1 + 2} rows"
            SQUARES = [n * n for n in range(3)]
            SEEN = {n for n in range(3)}
            BY = {n: n for n in range(3)}
            PICKED: set = {1}

            @staticmethod
            def _static(row):
                """S."""
                return row

            @classmethod
            def _made(cls):
                """M."""
                return cls._static(1)

            @property
            def _limit(self):
                """L."""
                return 3

            @_limit.setter
            def _limit(self, value):
                """S."""

            @functools.cached_property
            def _cached(self):
                """C."""
                return 1

            @pytest.fixture
            def _rows(self):
                """R."""
                return [1]

            @pytest.mark.slow
            @mock.patch("os.getcwd")
            @mock.patch.object(re, "compile")
            def _patched(self, compiled, getcwd):
                """P."""
                return compiled, getcwd

            @abc.abstractmethod
            def _abstract(self):
                """A."""
            '''), "    ")
        root = build(tmp_path, _split_class_source(member).replace(
            "import re\n", "import abc\nimport functools\nimport re\nfrom unittest import mock\n",
            1))
        assert render_all(layout_of(root), line_limit(root))

    def test_a_decorated_class(self, tmp_path):
        """Its marks would have to be repeated on every part."""
        source = sample_with("class TestBig:", "@pytest.mark.slow\nclass TestBig:")
        assert "a decorated class" in refusal(tmp_path, source)

    def test_slices_out_of_source_order(self, tmp_path):
        """A slice is a contiguous run, in the order the plan lists the slices."""
        plan = plan_dict()
        plan["parts"][1]["nodes"][0]["tests"] = ["test_b", "test_a"]
        assert "out of order" in refusal(tmp_path, plan=plan)

    def test_a_test_left_out_of_every_slice(self, tmp_path):
        """Every test of a split class is in exactly one slice."""
        plan = plan_dict()
        plan["parts"][2]["nodes"][0]["tests"] = ["test_c", "test_d"]
        assert "missing ['test_e']" in refusal(tmp_path, plan=plan)

    def test_a_test_reaching_another_slice_through_self(self, tmp_path):
        """test_a calling test_c would find it on neither its class nor the mixin."""
        source = sample_with("assert self._shared() == 2",
                             "assert self._shared() == 2 and self.test_c")
        assert "reaches ['test_c'] through self" in refusal(tmp_path, source)

    @pytest.mark.parametrize("part, body", [
        (1, "TestBig.flag = 1"), (1, "assert TestBig.__doc__"), (0, "assert TestBig"),
    ], ids=["a-write-in-a-part-holding-a-slice", "a-read-in-a-part-holding-a-slice",
            "a-read-in-another-part"])
    def test_a_node_outside_the_class_naming_it(self, tmp_path, part, body):
        """In a part holding a slice the name reaches that slice, not the class (a write
        there misses every other slice); in another part it has no single home."""
        plan = plan_dict()
        plan["parts"][part]["nodes"].append("test_names_it")
        source = SAMPLE + f'\n\ndef test_names_it():\n    """N."""\n    {body}\n'
        assert "test_names_it (to " in refusal(tmp_path, source, plan)
        assert "reads the split class ['TestBig'], of which each part holds one slice" in (
            refusal(tmp_path, source, plan))

    def test_a_class_without_a_docstring(self, tmp_path):
        """The mixin carries the class's docstring; there must be one."""
        source = sample_with('    """The big class, which is split across two parts."""\n', "")
        assert "needs a docstring" in refusal(tmp_path, source)


class TestThePlanIsRead:
    """A malformed plan is refused before any source is read, and a plan read keeps the
    sha256 of the bytes it parsed."""

    @pytest.mark.parametrize("change, why", [
        (lambda p: p.update(extra=1), "unknown ['extra']"),
        (lambda p: p["parts"].pop(), None),
        (lambda p: p["parts"][0].update(module="conftest"), "not a usable module name"),
        (lambda p: p["parts"][1]["nodes"][0].update(docstring="two\nlines"), "ONE line"),
        (lambda p: p.update(source="/abs/test_x.py"), "repository-relative"),
    ])
    def test_a_malformed_plan(self, change, why):
        """Each is refused by the plan reader, with its reason."""
        plan = plan_dict()
        change(plan)
        if why is None:
            plan["parts"] = plan["parts"][:1]
            why = "at least two parts"
        with pytest.raises(PlanError, match=why.replace("[", r"\[").replace("]", r"\]")):
            plan_of(plan)

    def test_a_plan_keeps_the_sha256_of_the_bytes_it_parsed(self, tmp_path):
        """One bytes object is decoded, parsed and hashed (the coordinator's ruling on 3C-2's
        report): with CRLF line endings, which the decoded text reads as LF, the plan's
        sha256 is the file's bytes', not its text's."""
        data = json.dumps(plan_dict(), indent=1).replace("\n", "\r\n").encode("utf-8")
        path = tmp_path / "x.plan.json"
        path.write_bytes(data)
        text = data.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
        assert load_plan(path).sha256 == hashlib.sha256(data).hexdigest()
        assert load_plan(path).sha256 != hashlib.sha256(text).hexdigest()
