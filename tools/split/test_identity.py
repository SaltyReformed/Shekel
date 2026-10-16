"""The identity screen's grammar (:mod:`tools.split._identity`), each rule shown refusing the
spelling it exists for, and its controls accepted: the delta review's H-B items 1-8 and L-D
(the coordinator's ruling, 2026-10-15), M-C's claims about the M2 screen, Josh's answers to
the census's three forks (2026-10-15), and the routes to the module being split that the
census session found accepted."""
from __future__ import annotations

import textwrap

import pytest

from tools.split.conftest import accepted, refusal, sample_with

#: TestFirst.test_one's docstring line, after which a case's body is written (TestFirst is
#: moved whole to test_first).
_TEST_ONE = '        """One."""\n'

#: TestBig._shared's last line, before which a case's body is written (_shared is a member of
#: the split class, so it moves to the mixin in _helpers.py).
_SHARED = "        return helper_two()\n"

#: The autouse fixture's body, which a case's body replaces (the fixture then requests
#: ``request``; it moves to the package's conftest.py).
_FIXTURE = '    monkeypatch.setenv("SAMPLE", "1")\n'


def in_test_one(body: str, imports: str = "") -> str:
    """The sample with ``body`` written in TestFirst.test_one and ``imports`` added to the
    module's import block."""
    source = sample_with(_TEST_ONE, _TEST_ONE + textwrap.indent(body, " " * 8) + "\n")
    return source.replace("import re\n", imports + "import re\n", 1)


def in_split_member(body: str) -> str:
    """The sample with ``body`` written in TestBig._shared."""
    return sample_with(_SHARED, textwrap.indent(body, " " * 8) + "\n" + _SHARED)


def in_split_class(member: str) -> str:
    """The sample with ``member`` (a whole member, indented as one) added to TestBig."""
    docstring = '    """The big class, which is split across two parts."""\n'
    return sample_with(docstring, docstring + "\n" + member + "\n")


def in_whole_class(member: str) -> str:
    """The sample with ``member`` added to TestFirst, which is moved whole."""
    return sample_with('    """First tests."""\n', '    """First tests."""\n\n' + member + "\n")


def in_fixture(body: str) -> str:
    """The sample's autouse fixture running ``body``, with ``request`` requested."""
    return sample_with(_FIXTURE, textwrap.indent(body, "    ") + "\n").replace(
        "def _frozen(monkeypatch):", "def _frozen(monkeypatch, request):")


#: Every identity read the review measured ACCEPTED by a5d390943's screen (H-B items 1-8):
#: ``(id, body written in test_one, module imports, the refusal it must now meet)``.
_REVIEW_SPELLINGS = [
    ("1-a-name-bound-by-a-foreign-import-and-by-the-source",
     "import tests.test_area.test_sample\nassert tests.test_area.test_sample.__file__",
     "import tests.shared\n", "__file__ may name or reach where the code runs"),
    ("2-import-module", 'assert importlib.import_module("tests.test_area.test_sample").__file__',
     "import importlib\n", "__file__ may name or reach where the code runs"),
    ("2-sys-modules", 'assert sys.modules["tests.test_area.test_sample"].__file__',
     "import sys\n", "__file__ may name or reach where the code runs"),
    ("2-getattr-sys-modules",
     'assert getattr(sys.modules["tests.test_area.test_sample"], "__file__")',
     "import sys\n", "__file__ may name or reach where the code runs"),
    ("2-inspect-on-import-module",
     'assert inspect.getfile(importlib.import_module("tests.test_area.test_sample"))',
     "import importlib\nimport inspect\n", "inspect.getfile reaches inspect"),
    ("2-exc-info", "assert sys.exc_info()[2].tb_frame.f_globals", "import sys\n",
     "f_globals may name or reach where the code runs"),
    ("3-the-top-package", "import tests\nassert tests.test_area.test_sample.__file__", "",
     "__file__ may name or reach where the code runs"),
    ("3-the-parent-package",
     "from tests import test_area\nassert test_area.test_sample.__file__", "",
     "__file__ may name or reach where the code runs"),
    ("4-an-import-name-shadowed", "re = type(self)\nassert re.__module__", "",
     "re.__module__ may name or reach where the code runs"),
    ("4-a-name-imported-and-assigned", "import json\njson = type(self)\nassert json.__module__",
     "", "json.__module__ may name or reach where the code runs"),
    ("4-a-comprehension-variable-shadowing-an-import",
     "import re\nassert [re.__module__ for re in [type(self)]]", "",
     "re.__module__ may name or reach where the code runs"),
    ("5-a-local-relative-import", "from . import test_sample as me\nassert me.__file__", "",
     "a relative import inside moved code"),
    ("6-an-alias-of-an-inspect-function", "f = inspect.getfile\nassert f(type(self))",
     "import inspect\n", "inspect.getfile reaches inspect"),
    ("6-an-alias-of-inspect", "ins = inspect\nassert ins.getfile(type(self))",
     "import inspect\n", "inspect reaches inspect"),
    ("6-getattr-off-inspect", 'assert getattr(inspect, "getfile")(type(self))',
     "import inspect\n", "inspect reaches inspect"),
    ("8-a-class-namespace-key", 'assert type(self).__dict__["__module__"]', "",
     "__dict__ may name or reach where the code runs"),
    ("8-vars-key", 'assert vars(type(self))["__module__"]', "",
     "the string '__module__' names an attribute the screen refuses"),
]


@pytest.mark.parametrize("body, imports, why", [case[1:] for case in _REVIEW_SPELLINGS],
                         ids=[case[0] for case in _REVIEW_SPELLINGS])
def test_every_spelling_the_review_measured_is_refused(tmp_path, body, imports, why):
    """Each read the delta review found ACCEPTED (H-B items 1-6 and 8) is refused by the rule
    the grammar names for it."""
    assert why in refusal(tmp_path, in_test_one(body, imports))


#: Every read that hands back a holder (a namespace, a carrier, a frame or the import
#: system), hand-listed rather than read from the screen's set.
_HOLDERS = ["__dict__", "__globals__", "__builtins__", "__self__", "__closure__",
            "cell_contents", "__getattribute__", "__getattr__", "__spec__", "__loader__",
            "f_globals", "f_locals", "f_back", "tb_frame", "tb_next", "__traceback__",
            "gi_frame", "cr_frame", "ag_frame"]

#: Every read that hands back a value naming a place, hand-listed.
_PLACES = ["__file__", "__cached__", "__package__", "__path__", "__module__",
           "__firstlineno__", "__code__", "co_filename", "co_firstlineno", "co_lines",
           "co_positions", "co_linetable", "co_lnotab", "co_qualname", "f_code", "f_lineno",
           "tb_lineno", "gi_code", "cr_code", "ag_code", "cr_origin"]


class TestTheExemptShapesGrammar:
    """Rule 3 as a grammar (the coordinator's ruling on 3C-3's Fork 1, 2026-10-16): off an
    import of another module only a read handing back a VALUE naming its place is exempt; a
    read handing back a namespace, a carrier, a frame or the import system is refused off
    every root, the exempt shape included."""

    @pytest.mark.parametrize("attribute", _HOLDERS)
    def test_a_holder_read_off_an_import_of_another_module_is_refused(self, tmp_path,
                                                                       attribute):
        """Each was exempt, so ACCEPTED, by f3690b221's screen."""
        assert f"json.loads.{attribute} may name or reach where the code runs: it hands" in (
            refusal(tmp_path, in_test_one(f"assert json.loads.{attribute}", "import json\n")))

    @pytest.mark.parametrize("attribute", _PLACES)
    def test_a_place_read_off_an_import_of_another_module_is_accepted(self, tmp_path,
                                                                       attribute):
        """Another module's object is not relocated by the move, so its place reads the
        same."""
        accepted(tmp_path, in_test_one(f"assert json.loads.{attribute}", "import json\n"))

    @pytest.mark.parametrize("body, imports, spelled", [
        ('assert sys.__dict__["modules"]', "import sys\n", "sys.__dict__"),
        ('assert os.__dict__["sys"].modules', "import os\n", "os.__dict__"),
        ('assert os.path.join.__globals__["sys"].modules', "import os\n",
         "os.path.join.__globals__"),
        ("assert sys.exit.__self__.modules", "import sys\n", "sys.exit.__self__"),
        ('assert os.__getattribute__("sys").modules', "import os\n", "os.__getattribute__"),
        ('name = "__imp" + "ort__"\nassert json.loads.__builtins__[name]("tests.shared")',
         "import json\n", "json.loads.__builtins__"),
    ], ids=["a-modules-namespace", "a-module-held-in-a-namespace", "a-functions-globals",
            "a-builtins-owner", "a-getter-by-name", "a-builtins-namespace-by-a-computed-key"])
    def test_each_route_to_the_import_system_the_probe_measured_is_refused(
            self, tmp_path, body, imports, spelled):
        """Each reaches ``sys.modules`` or ``__import__`` off an import of another module,
        and each was ACCEPTED by f3690b221's screen (3C-3's probe, 2026-10-16; the last one
        with its key computed, which no string rule sees)."""
        assert f"{spelled} may name or reach where the code runs: it hands" in refusal(
            tmp_path, in_test_one(body, imports))

    @pytest.mark.parametrize("body, spelled", [
        ("coro = _make(1)\nassert coro.cr_origin", "coro.cr_origin"),
        ("assert type(self).__firstlineno__", "type(self).__firstlineno__"),
    ], ids=["cr_origin", "__firstlineno__"])
    def test_a_place_read_off_anything_but_an_import_is_refused(self, tmp_path, body,
                                                                spelled):
        """Delta review 2's Low-1: a case for each member no test named."""
        assert f"{spelled} may name or reach where the code runs, and it is not read" in (
            refusal(tmp_path, in_test_one(body)))


#: The refusal every chain off the fixture ``request`` beyond its allowlist meets.
_BEYOND = "reads the fixture request beyond what moved code may"


class TestTheRequestHalf:
    """Rule 5's ``request`` half: the fixture only as a chain's root or a callee, and every
    chain off it within its ALLOWLIST (the coordinator's ruling on delta review 2's High-1):
    ``request.param`` and what follows it, or ``request.node.get_closest_marker(...)`` /
    ``add_marker(...)`` called.  The first four tests' shapes were refused until 3C-3b for a
    carrier or location set's reason; the sets are deleted by that ruling, so each shape is
    kept and its reason is the allowlist's."""

    def test_a_node_held_under_another_name(self, tmp_path):
        """``node = request.node; node.path`` read the location of the requesting test."""
        assert f"request.node {_BEYOND}" in refusal(
            tmp_path, in_fixture("node = request.node\nassert node.path"))

    def test_a_class_held_under_another_name_when_it_is_split(self, tmp_path):
        """``c = request.cls; c.__doc__`` read a slice's docstring."""
        assert f"request.cls {_BEYOND}" in refusal(
            tmp_path, in_fixture("c = request.cls\nassert c.__doc__"))

    @pytest.mark.parametrize("member", [
        "module", "fspath", "path", "nodeid", "location", "reportinfo", "parent", "getparent",
        "listchain", "iter_parents", "keywords", "ihook",
    ])
    def test_every_location_member_is_refused(self, tmp_path, member):
        """M-C: the location set a5d390943 named, each member off the node."""
        assert f"request.node.{member}.name {_BEYOND}" in refusal(
            tmp_path, in_fixture(f"assert request.node.{member}.name"))

    def test_a_private_member_is_refused(self, tmp_path):
        """A private attribute of ``request`` (``_pyfuncitem`` is the test's own node)."""
        assert f"request._pyfuncitem {_BEYOND}" in refusal(
            tmp_path, in_fixture("assert request._pyfuncitem"))

    @pytest.mark.parametrize("body, spelled", [
        ("assert request.fixturenames", "request.fixturenames"),
        ('found = request.node.funcargs["request"]\nassert found', "request.node.funcargs"),
        ("items = request.node.session.items\nassert items", "request.node.session.items"),
        ("assert request.node.session.name", "request.node.session.name"),
        ("assert request.cls.__name__", "request.cls.__name__"),
        ("assert request.config", "request.config"),
        ("assert request.fixturename", "request.fixturename"),
        ("assert request.node.name", "request.node.name"),
        ("assert request.node.get_closest_marker", "request.node.get_closest_marker"),
        ("marker = request.node.add_marker\nassert marker", "request.node.add_marker"),
    ], ids=["fixturenames", "funcargs-hands-the-request-back", "every-collected-path",
            "the-session", "a-split-class-name", "config", "fixturename", "the-node-name",
            "a-marker-read-not-called", "a-marker-adder-held"])
    def test_every_chain_beyond_the_allowlist_is_refused(self, tmp_path, body, spelled):
        """Each was ACCEPTED by f3690b221's screen.  ``request.node.funcargs["request"] is
        request`` under pytest 9.1.1, and ``session.items`` holds every collected path
        (delta review 2's High-1); ``fixturenames`` carries which fixtures a location
        offers, so its control changes to refused (the coordinator's ruling on 3C-3's request
        allowlist, 2026-10-16); ``request.cls.__name__`` reads a slice's name (the subject of
        a test the allowlist retired: the read is now refused whether or not a class is
        split); ``session`` is delta review 2's Low-1 member, now held by the allowlist."""
        assert f"{spelled} {_BEYOND}" in refusal(tmp_path, in_fixture(body))

    def test_what_the_request_holds_beyond_its_place_is_accepted(self, tmp_path):
        """``request.param`` and what follows it, a marker read or added through the node
        (called), and ``tmp_path.parent`` read nothing the move changes."""
        accepted(tmp_path, in_fixture(
            "assert request.param and request.param.real and request.param[0]\n"
            'assert request.node.get_closest_marker("skip").kwargs\n'
            'request.node.add_marker("slow")'))
        source = sample_with("def test_top_level():\n", "def test_top_level(tmp_path):\n")
        (tmp_path / "other").mkdir()
        accepted(tmp_path / "other", source.replace("assert _START.year == 2026",
                                          "assert _START.year == 2026 and tmp_path.parent"))

    def test_flasks_request_is_not_the_fixture(self, tmp_path):
        """A ``request`` an import binds is Flask's request context, not held to rule 5: its
        ``path`` is a URL, and it may be passed."""
        accepted(tmp_path, in_test_one(
            "from flask import request\nassert request.path and str(request)"))


class TestSelfAndClsInASplitClass:
    """Rule 5's ``self`` / ``cls`` half applies in a split class's members, where the rules
    keyed on those names run, and nowhere else (Josh's answer to the census's second fork)."""

    def test_self_passed_out_of_a_split_member(self, tmp_path):
        """L-D: ``s = self; s.__doc__`` read a slice's docstring."""
        assert "self is used as a value" in refusal(
            tmp_path, in_split_member("s = self\nassert s.__doc__"))

    def test_cls_returned_by_a_split_member(self, tmp_path):
        """A classmethod handing its class out."""
        member = ('    @classmethod\n    def _kind(cls):\n        """K."""\n'
                  "        return cls")
        assert "cls is used as a value" in refusal(tmp_path, in_split_class(member))

    def test_a_method_handed_out_of_a_split_member(self, tmp_path):
        """A bound member's qualname names the mixin after the split, and its ``__self__``
        is ``self``."""
        assert "self.test_a hands out a method of a split class" in refusal(
            tmp_path, in_split_member("assert helper_one(self.test_a)"))

    def test_a_property_read_as_a_value_is_accepted(self, tmp_path):
        """A property hands out the value it computes, not a bound member."""
        member = '    @property\n    def _limit(self):\n        """L."""\n        return 3'
        source = in_split_class(member).replace(_SHARED,
                                                "        assert helper_one(self._limit)\n"
                                                + _SHARED)
        accepted(tmp_path, source)

    def test_self_returned_by_a_class_moved_whole_is_accepted(self, tmp_path):
        """A context manager's ``return self`` outside a split class: the identity set is
        refused off every root, so nothing keyed on ``self`` needs it."""
        member = '    def _me(self):\n        """Me."""\n        return self, type(self)'
        accepted(tmp_path, in_whole_class(member))


class TestARouteToTheModuleBeingSplit:
    """No moved code may hold its own module: after the split an import of the source names
    the package the split makes, so a write through it lands beside the code it meant to
    patch (the census session's probes, 2026-10-15)."""

    @pytest.mark.parametrize("body", [
        "import tests.test_area.test_sample as me\nme._START = 5",
        "from tests.test_area import test_sample\nassert test_sample._make(1)",
        "from tests.test_area.test_sample import _make\nassert _make(1)",
        "import tests.test_area.test_sample.test_first as part\nassert part.TestFirst",
        "from tests.test_area.test_sample.test_first import TestFirst\nassert TestFirst",
        "import tests.test_area\nassert tests.test_area",
    ], ids=["the-source-by-path", "the-source-from-its-package", "from-the-source",
            "a-module-of-the-package", "from-a-module-of-the-package", "a-package-holding-it"])
    def test_an_import_of_the_source_or_its_package(self, tmp_path, body):
        """M-C: from the source, and from or of a module of the package being written."""
        assert "reaches the module being split, or a package holding it" in refusal(
            tmp_path, in_test_one(body))

    def test_an_import_of_a_sibling_module_is_accepted(self, tmp_path):
        """``tests.shared`` is another module of the source's own package."""
        accepted(tmp_path, in_test_one("import tests.shared\nassert tests.shared.helper_one(1)"))

    def test_a_string_naming_the_source(self, tmp_path):
        """A dotted target is resolved by importing it."""
        assert "names the module being split" in refusal(
            tmp_path, in_test_one('assert "tests.test_area.test_sample._START"'))

    @pytest.mark.parametrize("text", [
        "tests.test_area:test_sample", "tests/test_area/test_sample.py", "test_sample",
    ], ids=["a-colon-spelling", "a-path", "the-bare-name"])
    def test_a_string_naming_the_source_in_any_spelling(self, tmp_path, text):
        """Judged by the census's own ``mentions`` (one home: the coordinator's ruling on
        delta review 2's High-2): each was ACCEPTED by f3690b221's dotted test, though
        ``pkgutil.resolve_name`` reads the colon spelling and ``getattr(tests.test_area,
        "test_sample")`` the bare name."""
        assert "names the module being split" in refusal(
            tmp_path, in_test_one(f"assert {text!r}"))

    def test_a_string_naming_a_module_of_the_new_package_is_accepted(self, tmp_path):
        """``mentions`` drops a continuation into the package, a correct pointer after the
        split (the coordinator's ruling, 3C-3b's Q2): moved code USING a module that does not
        exist yet fails before the split and passes after, which the proof's identical
        outcomes refuse; a message string carries no outcome."""
        accepted(tmp_path, in_test_one(
            'assert "tests.test_area.test_sample.test_first.TestFirst"'))

    def test_a_docstring_naming_the_source_is_accepted(self, tmp_path):
        """A bare string statement reaches nothing."""
        accepted(tmp_path, sample_with('"""A module-level test."""',
                                       '"""tests.test_area.test_sample.TestBig is the rest."""'))

    @pytest.mark.parametrize("body, why", [
        ("import os\nassert os.sys.modules", "os.sys.modules may reach sys.modules"),
        ("import os\nheld = os.sys", "may be the module sys held by another"),
        ("assert self.inspect.stack", "self.inspect.stack may reach inspect"),
        ("import gc\nassert gc.get_objects()", "gc.get_objects reaches gc.get_objects"),
        ("import gc\nheld = gc", "(the module gc) is used as a value"),
        ('import runpy\nassert runpy.run_module("json")', "runpy.run_module reaches"),
        ("assert _make.__builtins__", "_make.__builtins__ may name or reach"),
    ], ids=["a-re-export", "a-re-export-held", "off-any-root", "the-collector",
            "the-collector-held", "a-module-run-by-name", "a-functions-builtins"])
    def test_the_other_routes_the_standard_library_offers(self, tmp_path, body, why):
        """A re-export of a refused module, the collector's walks, ``runpy`` by name, and a
        function's builtins namespace."""
        assert why in refusal(tmp_path, in_test_one(body))

    def test_a_function_named_like_a_refused_module_is_accepted(self, tmp_path):
        """M-C: SQLAlchemy's ``inspect`` is called, and a module cannot be."""
        accepted(tmp_path, in_test_one("assert inspect(object())",
                                       "from sqlalchemy import inspect\n"))

    def test_the_collector_and_runpy_by_path_are_accepted(self, tmp_path):
        """``gc.collect()`` hands nothing back; ``runpy.run_path`` runs a file by path."""
        accepted(tmp_path, in_test_one('import gc\nimport runpy\ngc.collect()\n'
                                       'assert runpy.run_path'))


class TestTheImportSystem:
    """Rule 4's import-system arm with the calls the screen can resolve (Josh's answer to the
    census's third fork)."""

    @pytest.mark.parametrize("body", [
        'assert __import__("datetime").date',
        'assert __import__("datetime", fromlist=["date"]).date',
        'import importlib\nassert importlib.import_module("json")',
        "import importlib\nimport json\nassert importlib.reload(json)",
        "from importlib import reload\nimport json\nassert reload(json)",
        'assert __import__("app.models.pay_period", fromlist=["PayPeriod"]).PayPeriod',
    ], ids=["import-a-constant", "import-a-constant-from", "import-module-a-constant",
            "reload-an-imported-module", "reload-imported-by-name",
            "import-a-model-from-its-module"])
    def test_a_provable_import_is_accepted(self, tmp_path, body):
        """A constant name outside the source, or a reload of an imported module, does the
        same from any file."""
        accepted(tmp_path, in_test_one(body))

    @pytest.mark.parametrize("body, why", [
        ('assert __import__("tests.shared")', "__import__ reads, imports"),
        ('assert __import__("tests.test_area", fromlist=["test_sample"])',
         "__import__ reads, imports"),
        ('name = "date"\nassert __import__("datetime", fromlist=[name])',
         "__import__ reads, imports"),
        ('import importlib\nname = "json"\nassert importlib.import_module(name)',
         "importlib.import_module reaches importlib"),
        ('import importlib\nassert importlib.import_module("tests.test_area.test_sample")',
         "importlib.import_module reaches importlib"),
        ('import importlib\nassert importlib.import_module(".shared")',
         "importlib.import_module reaches importlib"),
        ('import importlib\nassert importlib.import_module("json", "tests")',
         "importlib.import_module reaches importlib"),
        ("import importlib\nimport json\nheld = json\nassert importlib.reload(held)",
         "importlib.reload reaches importlib"),
        ('import importlib.util\nassert importlib.util.find_spec("json")',
         "importlib.util.find_spec reaches importlib"),
    ], ids=["the-top-package-holds-the-source", "from-a-package-holding-the-source",
            "a-fromlist-computed", "a-computed-name", "the-source-by-name", "a-relative-name",
            "a-package-argument", "a-reload-of-a-local", "another-member"])
    def test_every_other_import_is_refused(self, tmp_path, body, why):
        """Each names, or may name, the source or something the screen cannot resolve."""
        assert why in refusal(tmp_path, in_test_one(body))


@pytest.mark.parametrize("body, why", [
    ('import pkgutil\nassert pkgutil.resolve_name("json:loads")',
     "pkgutil.resolve_name reaches pkgutil"),
    ("import sys\nassert sys.path_importer_cache",
     "sys.path_importer_cache reaches sys.path_importer_cache"),
    ("import sys\nassert sys.path_hooks", "sys.path_hooks reaches sys.path_hooks"),
    ("import sys\nassert sys.meta_path", "sys.meta_path reaches sys.meta_path"),
    ("import sys\nassert sys.last_exc", "sys.last_exc reaches sys.last_exc"),
], ids=["pkgutil", "the-finders", "the-path-hooks", "the-meta-path", "the-last-exception"])
def test_the_import_systems_other_doors_are_refused(tmp_path, body, why):
    """``pkgutil`` and the finders and hooks that resolve a module by a computed name, each
    ACCEPTED by f3690b221's screen (delta review 2's High-2); and ``sys.last_exc``, delta
    review 2's Low-1 member."""
    assert why in refusal(tmp_path, in_test_one(body))


class TestAnAttributeNamedAtRunTime:
    """Rule 4's by-name arm: a READ by a computed name is refused off anything but an exempt
    chain; a WRITE is not (Josh's answer to the census's first fork)."""

    @pytest.mark.parametrize("body, why", [
        ('name = "real"\nassert getattr(1, name)', "getattr with a name computed at run time"),
        ('name = "real"\nassert hasattr(1, name)', "hasattr with a name computed at run time"),
        ('import operator\nname = "real"\nassert operator.attrgetter(name)(1)',
         "operator.attrgetter reads an attribute named at run time"),
        ('from operator import methodcaller\nname = "bit_length"\nassert methodcaller(name)(1)',
         "methodcaller reads an attribute named at run time"),
        ('import json\nname = "__glo" + "bals__"\nassert getattr(json.loads, name)',
         "getattr with a name computed at run time, off json.loads"),
        ('import json\nname = "loads"\nassert hasattr(json, name)',
         "hasattr with a name computed at run time, off json"),
    ], ids=["getattr", "hasattr", "attrgetter", "methodcaller", "getattr-off-an-import",
            "hasattr-off-an-import"])
    def test_a_read_by_a_computed_name_is_refused(self, tmp_path, body, why):
        """The screen cannot know which attribute is read; off an import of another module it
        may be a holder (the coordinator's ruling on 3C-3's F-a, 2026-10-16: the two last
        cases were ACCEPTED by f3690b221's screen)."""
        assert why in refusal(tmp_path, in_test_one(body))

    def test_a_write_by_a_computed_name_and_a_constant_getter_are_accepted(self, tmp_path):
        """setattr / delattr write; attrgetter with constant names is judged name by name."""
        accepted(tmp_path, in_test_one(
            'import operator\nimport types\nrow = types.SimpleNamespace()\n'
            'for name, value in {"a": 1}.items():\n    setattr(row, name, value)\n'
            '    delattr(row, name)\nassert operator.attrgetter("real")(1) == 1'))


@pytest.mark.parametrize("body, imports, why", [
    ('assert dict(inspect.getmembers(os))["sys"].modules', "import inspect\nimport os\n",
     "inspect.getmembers reaches inspect"),
    ("assert inspect.getclosurevars(json.dumps).globals", "import inspect\nimport json\n",
     "inspect.getclosurevars reaches inspect"),
    ("assert inspect.getmodule(json.dumps)", "import inspect\nimport json\n",
     "inspect.getmodule reaches inspect"),
], ids=["the-members-of-a-module", "a-functions-globals", "a-functions-module"])
def test_an_inspect_call_beyond_its_allowlist_is_refused(tmp_path, body, imports, why):
    """Each hands back a namespace or a module, and each was ACCEPTED by f3690b221's screen
    with its arguments exempt (the coordinator's ruling on 3C-3's F-b, 2026-10-16)."""
    assert why in refusal(tmp_path, in_test_one(body, imports))


def test_the_inspect_allowlist_off_imports_is_accepted(tmp_path):
    """``getsource``, ``getfile`` and ``signature``: the three ``tests/`` calls, each handing
    back text or a value read off another module's object."""
    accepted(tmp_path, in_test_one(
        "assert inspect.getsource(re) and inspect.getfile(re) and inspect.signature(re.match)",
        "import inspect\n"))


def test_inspect_needs_every_argument_exempt(tmp_path):
    """M-C: ``inspect.<fn>(...)`` is accepted only when EVERY argument is an exempt chain."""
    assert "inspect.getsource reaches inspect" in refusal(
        tmp_path, in_test_one("assert inspect.getsource(re, self)", "import inspect\n"))
    accepted(tmp_path, in_test_one("assert inspect.getsource(re)", "import inspect\n"))


def test_a_name_any_import_of_the_source_binds_is_never_exempt(tmp_path):
    """``tests`` is bound where test_one reads it only by ``import tests.shared``, but another
    node imports the source through the same name: rule 3 holds the NAME never foreign."""
    source = in_test_one("assert tests.shared.__file__", "import tests.shared\n").replace(
        "    assert _START.year == 2026",
        "    import tests.test_area.test_sample\n    assert _START.year == 2026", 1)
    assert "tests.shared.__file__ may name or reach where the code runs" in refusal(
        tmp_path, source)


def test_a_default_is_read_where_the_function_is_defined(tmp_path):
    """A default, like a decorator, is evaluated in the enclosing scope, where ``re`` is the
    module's import (exempt), though inside the function ``re`` is a parameter."""
    member = '    def _f(self, re=re.__file__):\n        """F."""\n        return re'
    accepted(tmp_path, in_whole_class(member))


def test_a_name_read_where_no_symbol_table_matches_is_held_to_every_rule(tmp_path):
    """Two lambdas on one line share a name and a line, so neither's symbol table can be
    matched: a name read in one is judged by every import of it in the source."""
    assert "sys.modules reaches sys.modules" in refusal(tmp_path, in_test_one(
        "pair = (lambda: sys.modules, lambda: 1)\nassert pair", "import sys\n"))
