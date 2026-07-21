"""Package privacy: ``shekel-private-module-import`` (W9910).

The structural gate of the balance arc's Phase D
(``docs/audits/balance_architecture/README.md``, step D-gate): **a package's
private modules are private.** A module outside package ``P`` may not import
``P._x``, nor any name from it, in any spelling:

* ``from P._x import name`` -- the form pylint's stock ``import-private-name``
  extension (C2701) does NOT flag (measured on pylint 4.0.5, finding N-26),
  which is why this checker exists;
* ``from P import _x`` where ``_x`` is a module of ``P``;
* ``import P._x``, aliased or not.

Relative imports are resolved against the importing module's own dotted name
first, so ``from . import _sibling`` is inherently intra-package and a
``from .._x import name`` that climbs out of ``_x``'s owning package is caught
like its absolute twin. Imports under ``if TYPE_CHECKING:`` are deliberately
NOT exempt: a public signature typed from another package's private module is a
boundary leak, and an exemption there is precisely the shape finding N-25 is
made of.

"Inside package ``P``" is decided by the importer's dotted name -- or, as the
second chance, by its FILE: a module whose source file lives inside ``P``'s
own directory is a member of ``P`` no matter what the lint run named it. The
distinction is load-bearing for ``scripts/``, a PEP 420 namespace package
(no ``__init__.py``): pylint names its entry points as TOP-LEVEL modules
(``rotate_sessions``, not ``scripts.rotate_sessions``), yet the files sit
beside ``scripts/_script_lib.py`` and are exactly the siblings that shared
library exists for. Membership is granted PER BOUNDARY, never per statement:
a ``scripts/`` sibling is a member of ``scripts`` but not of a private
subpackage inside it, so a deeper crossing still flags. The file test
resolves each owner through astroid and only ever affirms on a real on-disk
directory match, so an unresolvable owner still fails closed.

Unlike the W9906/W9909 name fences, the rule is name-INDEPENDENT and
fail-closed by construction: it consults no producer list and no allowlist --
only the imported dotted path and the importing module's own name -- so there
is nothing to keep complete and nothing to rot. It is what lets the name
fences delete (plan step D3) instead of being maintained forever.

What the rule deliberately does NOT cover, stated so the boundary is known
rather than discovered:

* a private NAME defined in a public module (``from pkg.mod import _helper``)
  -- package-private by convention, not a module boundary; the module-vs-name
  split is decided by astroid resolution, failing CLOSED when the ``from``
  target itself cannot be resolved;
* attribute access on a legitimately imported package (``pkg._x`` after
  ``import pkg``) -- stock ``protected-access`` (W0212) plus the 10.00/10
  floor cover it;
* dynamic import (``importlib`` / ``__import__``) -- no static gate sees
  reflection.
"""

import os

from astroid import AstroidBuildingError, nodes

from pylint.checkers import BaseChecker


def _is_private_segment(segment: str) -> bool:
    """Return True when ``segment`` is a private (single-underscore) name.

    Dunder names (``__main__``, ``__version__``) are public by Python
    convention and are not treated as private.

    Args:
        segment: One dotted-path component or imported name.

    Returns:
        ``True`` when the segment marks a private module or name.
    """
    return segment.startswith("_") and not (
        segment.startswith("__") and segment.endswith("__")
    )


def _privacy_crossing(root: nodes.Module, path: str) -> "tuple[str, str] | None":
    """Return the first privacy boundary ``path`` crosses for ``root``'s module.

    Walks ``path``'s dotted segments; a private segment marks a private module
    of the package named by the segments before it, and only that package's own
    members may import it. Membership of EACH owner is decided independently:
    the importer's dotted name (the package itself or anything under it), or --
    the namespace-package second chance -- its FILE lying inside that owner's
    directory (:func:`_importer_file_inside`). Deciding per BOUNDARY rather
    than per statement is load-bearing: a ``scripts/`` entry point is a member
    of ``scripts`` but NOT of a hypothetical ``scripts._libpkg``, so
    ``from scripts._libpkg._deep import f`` must still flag at the deeper
    boundary even though the shallow one is the importer's own (found in this
    step's adversarial review; a statement-wide suppression passed it).

    A TOP-LEVEL private module (``__future__``, ``_socket``) has no owning
    package, so it never crosses a boundary. An importer with an empty name
    and no real file is inside nothing and therefore flags -- the fail-closed
    direction every fence in this plugin takes.

    Args:
        root: The importing module (the linted file's root node).
        path: Absolute dotted path being imported.

    Returns:
        ``(private_module, owner_package)`` for the first crossing, or ``None``
        when the importer is a member of every boundary the path touches.
    """
    importer = root.name or ""
    segments = path.split(".")
    for index, segment in enumerate(segments):
        if index == 0 or not _is_private_segment(segment):
            continue
        owner = ".".join(segments[:index])
        if importer == owner or importer.startswith(owner + "."):
            continue
        if _importer_file_inside(root, owner):
            continue
        return ".".join(segments[: index + 1]), owner
    return None


def _absolute_base(node: nodes.ImportFrom) -> "str | None":
    """Resolve the ``from`` clause of ``node`` to an absolute dotted path.

    An absolute import is returned as written. A relative import is resolved
    against the enclosing module's own dotted name (one leading dot names the
    current package, each further dot climbs one level), using the module's
    ``package`` flag so an ``__init__`` and a plain module resolve from the
    right starting depth.

    Args:
        node: The ``ImportFrom`` node under inspection.

    Returns:
        The absolute dotted path the clause targets, or ``None`` when it
        cannot be resolved: the enclosing module's name is unknown, or the
        import climbs past the top level (pylint's own E0402 territory).
    """
    level = node.level or 0
    if not level:
        return node.modname or ""
    root = node.root()
    importer = root.name or ""
    if not importer:
        return None
    parts = importer.split(".")
    if not root.package:
        parts = parts[:-1]
    if level > len(parts):
        return None
    parts = parts[: len(parts) - (level - 1)]
    base = ".".join(parts)
    if node.modname:
        base = f"{base}.{node.modname}" if base else node.modname
    return base


def _importer_file_inside(root: nodes.Module, owner: str) -> bool:
    """Return whether ``root``'s source file lives inside package ``owner``.

    The second-chance membership test the module docstring describes: pylint
    names a namespace package's entry points as top-level modules (``scripts/``
    has no ``__init__.py``, so ``scripts/rotate_sessions.py`` lints as
    ``rotate_sessions``), which makes the dotted-name test alone blind to the
    fact that the file sits INSIDE the owning directory. The owner package is
    resolved through astroid; a regular package's directory is its
    ``__init__``'s parent, a namespace package carries its directories in
    ``path``. Every failure returns ``False``, and only paths that EXIST on
    disk participate (astroid's ``"<?>"`` string-build placeholder must not
    resolve to the working directory) -- this test can only SUPPRESS a finding
    on an affirmative match, never create an exemption from silence.

    Args:
        root: The importing module (the linted file's root node).
        owner: Fully-qualified name of the owning package to test against.

    Returns:
        ``True`` when the importing file is inside ``owner``'s directory.
    """
    source = root.file or ""
    if not source or not os.path.isfile(source):
        # A string-built module carries astroid's "<?>" placeholder, not a
        # path. Only a real on-disk file can prove membership; the placeholder
        # -- and a placeholder-derived empty owner directory, filtered below --
        # must never do so, or a cached string-built owner would resolve to
        # the working directory and suppress real findings. The direct
        # ``_importer_file_inside`` unit test pins the pair.
        return False
    try:
        owner_module = root.import_module(owner, relative_only=False)
    except AstroidBuildingError:
        return False
    if owner_module.file:
        directories = [os.path.dirname(owner_module.file)]
    else:
        directories = list(getattr(owner_module, "path", None) or [])
    source_path = os.path.abspath(source)
    return any(
        source_path.startswith(os.path.abspath(directory) + os.sep)
        for directory in directories
        if directory and os.path.isdir(directory)
    )


class ShekelPackagePrivacyChecker(BaseChecker):
    """Forbid importing another package's private module, or any name from it.

    The boundary rule that makes structure do what the name fences policed:
    with every balance producer a private submodule of
    ``app.services.balance_at`` (plan steps D1d / D-ctx / D-fold), the ONE
    invariant left to enforce is that a private module is unreachable from
    outside its package -- for every package, in every import spelling, with
    no exemptions. The module docstring states the covered forms and the
    deliberate non-goals; the checker binds at :meth:`visit_import` and
    :meth:`visit_importfrom`, and resolves the ambiguous ``from P import _x``
    spelling with astroid (a module of ``P`` is fenced; a name defined in
    ``P``'s own code is package-private by convention and out of scope).
    """

    name = "shekel-package-privacy"
    msgs = {
        "W9910": (
            "Private module '%s' imported from outside its package '%s'; "
            "depend on the package's public surface instead",
            "shekel-private-module-import",
            "A package's private modules are private (balance arc Phase D, "
            "docs/audits/balance_architecture/README.md): every balance "
            "producer now lives as a private submodule of "
            "app.services.balance_at, so the one rule that makes that "
            "boundary structural -- for it and for every other package -- is "
            "that a module outside package P may not import P._x, nor any "
            "name from it. All spellings are covered: 'from P._x import "
            "name' (the form the stock import-private-name extension is "
            "fail-open for, finding N-26), 'from P import _x', and 'import "
            "P._x', aliased or not, relative or absolute, at top level or "
            "under 'if TYPE_CHECKING:' (a public signature typed from "
            "another package's private module is a boundary leak, finding "
            "N-25's shape, so type-checking imports are deliberately not "
            "exempt). The rule is name-independent and fail-closed: no "
            "allowlist, no producer list, nothing to keep complete. Out of "
            "scope by design: a private NAME defined in a public module "
            "(package-private by convention), attribute access on an "
            "imported package (stock protected-access W0212 plus the "
            "10.00/10 floor cover it), and dynamic import. Fix a finding by "
            "depending on the owning package's public surface; if the name "
            "you need has no public home, that is the package telling you "
            "where its API is missing.",
        ),
    }

    def visit_import(self, node: nodes.Import) -> None:
        """Flag ``import P._x`` (aliased or not) made outside package ``P``.

        ``node.names`` holds ``(dotted_path, alias)`` pairs; each dotted path
        is checked for a privacy crossing independently, so a multi-name
        import statement reports every violating path. Membership of each
        boundary -- dotted-name containment or the physical-file second
        chance -- is decided inside :func:`_privacy_crossing`.

        Args:
            node: The ``Import`` node under inspection.
        """
        for path, _alias in node.names:
            crossing = _privacy_crossing(node.root(), path)
            if crossing is not None:
                self.add_message(
                    "shekel-private-module-import", node=node, args=crossing,
                )

    def visit_importfrom(self, node: nodes.ImportFrom) -> None:
        """Flag a ``from`` import that reaches another package's private module.

        Two checks, in order. First the ``from`` clause itself: a private
        segment in the (resolved) base path whose owner the importer is not a
        member of is a crossing regardless of what names are imported --
        ``from P._x import anything`` leaks ``P._x``. Second, when every base
        boundary is the importer's own but the importer is not a member of the
        BASE package itself, each imported private name that astroid resolves
        to a module of that package is a crossing in the ``from P import _x``
        spelling; a private name that is NOT a module is package-private by
        convention and out of this rule's scope (see the module docstring).
        Falling through from the first check to the second is deliberate: a
        namespace-package sibling of ``P`` is a member of ``P`` but not of
        ``P._sub``, so ``from P._sub import _deeper`` must still reach the
        name scan after the base crossing dissolves. Membership of each
        boundary -- dotted-name or physical-file -- is decided in
        :func:`_privacy_crossing` / :func:`_importer_file_inside`. An
        unresolvable base (unknown importer, or a relative import past the
        top level) fails closed via :meth:`_flag_unresolvable`.

        Args:
            node: The ``ImportFrom`` node under inspection.
        """
        importer = node.root().name or ""
        base = _absolute_base(node)
        if base is None:
            self._flag_unresolvable(node)
            return
        crossing = _privacy_crossing(node.root(), base)
        if crossing is not None:
            self.add_message(
                "shekel-private-module-import", node=node, args=crossing,
            )
            return
        if importer == base or importer.startswith(base + "."):
            return
        for name, _alias in node.names:
            if not _is_private_segment(name):
                continue
            if not self._names_a_module(node, base, name):
                continue
            if _importer_file_inside(node.root(), base):
                continue
            self.add_message(
                "shekel-private-module-import",
                node=node,
                args=(f"{base}.{name}", base),
            )

    def _names_a_module(
        self, node: nodes.ImportFrom, base: str, name: str,
    ) -> bool:
        """Return whether ``base.name`` is a module rather than a defined name.

        ``from P import _x`` binds either a private SUBMODULE of package ``P``
        (in scope for this fence) or a private NAME defined in ``P``'s own code
        (package-private by convention, out of scope). Astroid resolution
        decides which, and each failure takes the fail-closed direction: when
        ``base`` itself cannot be resolved the target is presumed a module and
        flagged (a boundary the checker cannot see is not thereby open), while
        when ``base`` resolves and is not a package -- or ``base.name`` is not
        one of its modules -- the binding is a name.

        Args:
            node: The ``ImportFrom`` node, whose root supplies the resolution
                context.
            base: Absolute dotted path of the ``from`` clause.
            name: The private name being imported from ``base``.

        Returns:
            ``True`` when ``base.name`` is a module (or must be presumed one).
        """
        root = node.root()
        try:
            base_module = root.import_module(base, relative_only=False)
        except AstroidBuildingError:
            return True
        if not base_module.package:
            return False
        try:
            root.import_module(f"{base}.{name}", relative_only=False)
        except AstroidBuildingError:
            return False
        return True

    def _flag_unresolvable(self, node: nodes.ImportFrom) -> None:
        """Fail closed on a ``from`` import whose target cannot be resolved.

        Reached only when the enclosing module's name is unknown or a relative
        import climbs past the top level -- neither occurs in a real lint run
        over this repository, but a fence that cannot resolve a boundary must
        flag rather than exempt (the direction every fence in this plugin
        documents). Anything private in the clause -- a private segment in the
        relative module path, or a private imported name -- is reported with
        the unresolved prefix spelled as written.

        Args:
            node: The ``ImportFrom`` node whose base failed to resolve.
        """
        rendered = "." * (node.level or 0) + (node.modname or "")
        modname_segments = (node.modname or "").split(".") if node.modname else []
        if any(_is_private_segment(seg) for seg in modname_segments):
            self.add_message(
                "shekel-private-module-import",
                node=node,
                args=(rendered, "<unresolvable>"),
            )
            return
        prefix = rendered if rendered.endswith(".") else f"{rendered}."
        for name, _alias in node.names:
            if _is_private_segment(name):
                self.add_message(
                    "shekel-private-module-import",
                    node=node,
                    args=(f"{prefix}{name}", "<unresolvable>"),
                )
