"""Every GraphQL mutation quill's App sends goes through :meth:`_tracker.Board._write`.

``Board._write`` names the place's board as ``$p`` and no other, the fence that keeps a
write aimed at one tracker off another's board (``_tracker``'s module docstring).  It was
a fence by CONVENTION: a raw ``mutation`` string sent from :class:`_tracker.Tracker`, or
from a method of the board that bypasses ``_write``, passed every test (review of X-cx L7
A2, L2).  This test reads the source instead, so such a write fails here whatever it does.

**Its scope is the App's writes.**  ``_tracker`` is the one module that speaks to the
tracker as the App, so every mutation it holds must be a module-level constant whose every
use in it is the first argument of ``self._write`` inside :class:`_tracker.Board`, and no
call there hands ``graphql`` or ``graphql_lookup`` a mutation; every other module of the
tool holds no mutation at all.  ``setup_tracker`` is the one module outside that scope: it
configures the tracker as the DEVELOPER (``main`` connects with ``user_token()``), so its
mutations are his login's writes, which this fence was never about (``_github``'s
module docstring: the two identities).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

QUILL = Path(__file__).resolve().parent
#: A GraphQL mutation's text: the operation keyword first.
_MUTATION = re.compile(r"^\s*mutation\b")
#: The module that sends the App's writes, and the one that writes as the developer.
_TRACKER, _DEVELOPERS = "_tracker.py", "setup_tracker.py"
_SENDS = {"graphql", "graphql_lookup"}


def _modules() -> dict[str, ast.Module]:
    """Every module of the tool but its tests, parsed, by file name."""
    return {path.name: ast.parse(path.read_text(encoding="utf-8"))
            for path in sorted(QUILL.glob("*.py"))
            if not path.name.startswith("test_") and path != QUILL / "conftest.py"}


def _is_mutation(node: ast.AST) -> bool:
    """Whether ``node`` is, or is built from, a string constant that is a mutation."""
    return any(isinstance(part, ast.Constant) and isinstance(part.value, str)
               and _MUTATION.match(part.value) for part in ast.walk(node))


def _parents(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    """Each node's parent."""
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _inside(node: ast.AST, parents: dict, kind: type, name: str) -> bool:
    """Whether ``node`` sits inside a ``kind`` (class or function) named ``name``."""
    while node in parents:
        node = parents[node]
        if isinstance(node, kind) and node.name == name:
            return True
    return False


def fence_breaches(name: str, tree: ast.Module) -> list[str]:
    """Each way module ``name`` sends, or could send, a mutation around ``Board._write``.

    Args:
        name: The module's file name.
        tree: Its parsed source.

    Returns:
        One line per breach, naming its source line; empty when the module keeps the
        fence.
    """
    if name == _DEVELOPERS:
        return []
    parents = _parents(tree)
    constants = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and _is_mutation(node)]
    if name != _TRACKER:
        return [f"{name}:{node.lineno} holds a mutation outside {_TRACKER}" for node in constants]
    named = {target.id for statement in tree.body if isinstance(statement, ast.Assign)
             and _is_mutation(statement.value) for target in statement.targets
             if isinstance(target, ast.Name)}
    breaches = [f"{name}:{node.lineno} holds a mutation that is no module-level constant"
                for node in constants
                if not isinstance(parents.get(_statement(node, parents)), ast.Module)]
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id in named and isinstance(node.ctx, ast.Load)
                and not _board_writes(node, parents)):
            breaches.append(f"{name}:{node.lineno} uses the mutation {node.id} other than "
                            "as self._write's first argument inside Board")
        if _sends_mutation(node, named):
            breaches.append(f"{name}:{node.lineno} hands {node.func.attr} a mutation directly")
    return breaches


def _board_writes(name: ast.Name, parents: dict) -> bool:
    """Whether ``name`` is the first argument of a ``self._write(...)`` call inside ``Board``."""
    call = parents.get(name)
    if not (isinstance(call, ast.Call) and call.args and call.args[0] is name):
        return False
    method = call.func
    return (isinstance(method, ast.Attribute) and method.attr == "_write"
            and isinstance(method.value, ast.Name) and method.value.id == "self"
            and _inside(name, parents, ast.ClassDef, "Board"))


def _sends_mutation(node: ast.AST, named: set[str]) -> bool:
    """Whether ``node`` calls ``graphql`` or ``graphql_lookup`` with a mutation, written out
    or by the name of a module-level one, as its query."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SENDS and node.args):
        return False
    query = node.args[0]
    return _is_mutation(query) or (isinstance(query, ast.Name) and query.id in named)


def _statement(node: ast.AST, parents: dict) -> ast.AST:
    """The statement ``node`` belongs to."""
    while not isinstance(node, ast.stmt):
        node = parents[node]
    return node


def test_every_mutation_the_app_sends_goes_through_board_write():
    """The tool as committed keeps the fence, and it HAS mutations to keep it on: the
    board's four, each a module-level constant of ``_tracker``."""
    modules = _modules()
    assert {name: fence_breaches(name, tree) for name, tree in modules.items()
            if fence_breaches(name, tree)} == {}
    tracker = modules[_TRACKER]
    assert sorted(target.id for statement in tracker.body if isinstance(statement, ast.Assign)
                  and _is_mutation(statement.value) for target in statement.targets) == [
        "BOARD_ADD", "BOARD_AFTER", "BOARD_REMOVE", "BOARD_TOP"]


_PLANTED = '''
BOARD_ADD = "mutation($p: ID!) { addProjectV2ItemById(input: {projectId: $p}) { item { id } } }"

class Board:
    def _write(self, mutation, **variables):
        return self.github.graphql(mutation, p=self.board_id, **variables)

    def add(self, card):
        return self._write(BOARD_ADD, c=card)

class Tracker:
    def close_board(self):
        self.github.graphql("mutation($id: ID!) { deleteProjectV2(input: {projectId: $id}) "
                            "{ clientMutationId } }", id="PVT_other")

    def add_twice(self, card):
        return self.board._write(BOARD_ADD, c=card)

    def raw(self):
        self.github.rest("POST", "/graphql", {"query": "mutation { x }"})
'''


def test_a_planted_raw_mutation_is_a_breach_and_board_write_is_not():
    """The scanner, proven on planted source: a raw mutation sent from ``Tracker``, one sent
    through REST, and a board write reached from outside ``Board`` are each a breach; the
    board's own ``_write`` of its constant is not.  Outside ``_tracker`` any mutation is."""
    assert fence_breaches(_TRACKER, ast.parse(_PLANTED)) == [
        "_tracker.py:13 holds a mutation that is no module-level constant",
        "_tracker.py:20 holds a mutation that is no module-level constant",
        "_tracker.py:13 hands graphql a mutation directly",
        "_tracker.py:17 uses the mutation BOARD_ADD other than as self._write's first argument "
        "inside Board",
    ]
    assert fence_breaches("quill.py", ast.parse('Q = "mutation { x }"')) == [
        "quill.py:1 holds a mutation outside _tracker.py"]
