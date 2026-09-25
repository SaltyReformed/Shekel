"""Architecture test: every module that defines ``BalanceContext``'s methods is fenced.

Ruling **R-BAL146** split the read pass's three recurrence memos out of
``balance_at/_context.py`` into ``balance_at/_recurrence_memos.py``, as a mixin
``BalanceContext`` inherits.  Two gates see a MODULE rather than a class, and
the split's adversarial review measured a hole in each:

* **W9909** (``shekel-unclassified-fenced-export``) scopes a module by its exact
  name or a package prefix (``tools/pylint/shekel_checkers/balance_seam.py``,
  ``_fenced_module_ruling``).  A public method born in a NEW base-class module
  reaches every holder of the context unclassified: a probe method in the mixin
  drew no W9909 until the module joined ``_SEAM_PRIVATE_CONTEXT_MODULES``, and
  for a base module no registry names, no test fails.
* **pylint's ``no-member``** is off for a class matching its default
  ``mixin-class-rgx`` (``.*[Mm]ixin``), so a misspelled ``self.<field>`` in the
  mixin's bodies is silent where the same line on the class is ``E1101``.

What this test enforces
-----------------------

1. The modules defining ``BalanceContext`` and its bases are EXACTLY the two
   below, written out literally, so a new base module fails here and the
   message names the fence entry it needs.
2. Every ``self.<name>`` a base class reads is an attribute ``BalanceContext``
   has -- a dataclass field or a class attribute: the ``no-member`` check the
   mixin rule turns off.

**What it does not see**: ``getattr(self, "<name>")`` spelled with a string,
and a base class defined outside ``app/`` (there is none).
"""

import ast
import dataclasses
import inspect
import textwrap

from app.services.balance_at import BalanceContext

#: Written out LITERALLY: bound to ``_SEAM_PRIVATE_CONTEXT_MODULES``, emptying
#: that constant would empty this pin with it.
_FENCED_CONTEXT_MODULES = frozenset({
    "app.services.balance_at._context",
    "app.services.balance_at._recurrence_memos",
})


def _bases() -> list[type]:
    """``BalanceContext``'s base classes, ``object`` excluded."""
    return [cls for cls in BalanceContext.__mro__[1:] if cls is not object]


def test_every_module_defining_the_context_is_fenced():
    """A base class in a new module must join W9909's scope before it lands."""
    modules = {cls.__module__ for cls in (BalanceContext, *_bases())}
    assert modules == _FENCED_CONTEXT_MODULES, (
        f"BalanceContext is defined across {sorted(modules)}: add a new module to "
        "_SEAM_PRIVATE_CONTEXT_MODULES and give it its own ruling entry in "
        "tools/pylint/shekel_checkers/_fence_rulings.py, then name it here"
    )


def test_every_attribute_a_base_reads_exists_on_the_context():
    """The ``no-member`` check pylint's mixin rule turns off, restored."""
    known = (
        {field.name for field in dataclasses.fields(BalanceContext)}
        | set(dir(BalanceContext))
    )
    missing = []
    for cls in _bases():
        tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
        missing.extend(
            f"{cls.__name__}: self.{node.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr not in known
        )
    assert not missing, f"read on self but absent from BalanceContext: {missing}"
