"""AST helpers shared by more than one Shekel checker.

Kept in a single leaf module so each per-rule checker module depends only on
these primitives, never on a sibling checker module -- the package's SOLID
dependency direction is ``<rule module> -> _common``, and ``_common`` imports
nothing from the package. The names stay underscore-prefixed (package-private)
because they are an internal contract between the checker modules and their
unit tests, not a public API.
"""

from astroid import nodes


def _is_string_const(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a string literal constant."""
    return isinstance(node, nodes.Const) and isinstance(node.value, str)


def _called_name_in(node: nodes.Call, names: frozenset[str]) -> str | None:
    """Return the called name if it is in ``names``, else ``None``.

    Matches the bare-name import form (``apply_status_change(...)``) and the
    attribute form (``status_seam.apply_status_change(...)``) alike; name
    matching keeps the checker fast, and the guarded names are distinctive
    enough to carry no realistic collision risk.  ``node`` is the call
    expression under inspection.
    """
    func = node.func
    if isinstance(func, nodes.Name) and func.name in names:
        return func.name
    if isinstance(func, nodes.Attribute) and func.attrname in names:
        return func.attrname
    return None


def _module_in_allowlist(node: nodes.NodeNG, modules: frozenset[str]) -> bool:
    """Return True if ``node``'s enclosing module is in ``modules``.

    Matches the enclosing module's fully-qualified name (``node.root().name``)
    against ``modules`` exactly, or as a package prefix (``<module>.`` ...) so a
    module later split into a package keeps its submodules inside the set.
    Matching the FULL path -- not the basename -- means a same-named module in
    another package (a hypothetical ``app/routes/balance_at.py``) is NOT matched,
    so an allowlist cannot be silently bypassed by a name collision (a false
    negative is the dangerous mode for a fence).  An empty / unresolvable name
    matches nothing, so the call fails closed (is flagged): the safe direction.
    The trailing dot in the prefix test is required so a sibling like
    ``app.services.balance_resolver_helpers`` does not match
    ``app.services.balance_resolver``.

    Args:
        node: Any AST node; its ``root()`` module name is the value tested.
        modules: The frozenset of fully-qualified module names that are exempt.

    Returns:
        ``True`` if the enclosing module is allowlisted, else ``False``.
    """
    name = node.root().name or ""
    return any(
        name == module or name.startswith(module + ".")
        for module in modules
    )
