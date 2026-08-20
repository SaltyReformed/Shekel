"""Identity classes and decomposition leaves: the SHAPE of the step corpus.

Two derivations that :mod:`_registry` and :mod:`_order` both need, in one place
because they were THREE copies until 2026-08-11 and two of the three carried
the same blind spot.

**It is a sibling module rather than more of `_registry`, and that is this
project's own ruling on an over-ceiling module** (findings N-152 / N-156): the
same reason `_order` was split out when `_registry` reached 1,000 lines.  The
dependency runs one way -- both importers read this, it reads neither -- so the
graph stays acyclic, and the functions take a PROTOCOL rather than importing
:class:`_registry.StepRow`, which is what keeps that true structurally instead
of by comment.
"""
from __future__ import annotations

from typing import Protocol, TypeVar


class Step(Protocol):
    """The four members of a step row these derivations actually read.

    Stated as a protocol rather than imported so the no-reverse-dependency claim
    in this module's docstring is checkable rather than asserted: a concrete
    import from :mod:`_registry` would make the graph cyclic the moment that
    module imports this one, which it does.
    """

    @property
    def key(self) -> str:
        """The row's ``arc:id`` primary key."""

    @property
    def arc(self) -> str:
        """The arc column, which scopes a leaf's prefix match."""

    @property
    def ident(self) -> str:
        """The bare id, whose SUFFIX is what makes a row a leaf (rule 2)."""

    def alias_keys(self) -> list[str]:
        """The ``arc:id`` keys this row declares itself also known by."""


#: Bound to the protocol so a caller holding a concrete row type gets that
#: same type back, rather than the protocol, from :func:`identity_class`.
StepT = TypeVar("StepT", bound=Step)


def identity_class(parent: StepT, rows: list[StepT]) -> list[StepT]:
    """Return every row that is the SAME step as *parent*, itself included.

    **Membership is UNDIRECTED, and that is the whole correctness of it.**  An
    `also` cell is a DECLARATION, and so is the reverse cell: `pay_calendar:C2`
    naming `balance:X-l` says the same thing as `X-l` naming `C2`.  Reading only
    the parent's own cell makes the relation directional, and nothing in this
    gate requires it to be symmetric -- :func:`alias_violations` checks that an
    alias EXISTS and shares a tick state, never that it points back.  So a
    one-way cell would silently re-open the blind spot :func:`decomposition_leaf_keys`
    exists to close: blank `X-l`'s `also` cell and its stale `ticks with #10`
    goes green again, measured 2026-08-11 by an adversarial review of the fix.

    Args:
        parent: The row whose class to resolve.
        rows: Every step row.

    Returns:
        *parent* followed by every row declaring the relation in either
        direction, in table order.
    """
    return [parent, *(
        row for row in rows
        if row.key != parent.key
        and (row.key in parent.alias_keys() or parent.key in row.alias_keys())
    )]


def decomposition_leaf_keys(parent: Step, rows: list[Step]) -> list[str]:
    """Return the keys of *parent*'s decomposition leaves, across its CLASS.

    **The ONE derivation of "what are this container's leaves", and it was
    three copies until 2026-08-11** -- here, in :func:`decomposition_violations`
    inline, and in ``_order``.  Two of the three were per-arc, so a container
    whose leaves are filed under an identity SIBLING's name in ANOTHER arc
    answered nothing: `balance:X-l`, `pay_calendar:C2` and `recurrence:R-F12`
    are ONE step under three names and every leaf is a `C2-*` row, so two of the
    three containers were invisible to their own reconcilers.  A working-tree
    renumber then moved the class's tick rank and left two of its three rows
    stating the old one, with every gate green.

    **The asymmetry the previous derivation documented is preserved**: the
    parent set and the CLASS are both DECLARED, and only the leaf set is
    derived.  Deriving the parent set too would claim ``pay_calendar:C1`` as
    the parent of ``C10`` / ``C11`` / ``C12``, three unrelated steps; the
    specimen is derived by :func:`_staging.a_prefix_trap` rather than named
    here, because the one this sentence named until 2026-08-20 went stale.

    Args:
        parent: The container whose leaves to derive.
        rows: Every step row.

    Returns:
        Every leaf key of every member of the class, deduplicated, in row
        order.  Includes SHIPPED leaves -- the caller decides which it wants.
    """
    members = identity_class(parent, rows)
    member_idents = {(member.arc, member.ident) for member in members}
    seen: dict[str, None] = {}
    for member in members:
        for row in rows:
            if (
                row.arc == member.arc
                and (row.arc, row.ident) not in member_idents
                and row.ident.startswith(member.ident)
            ):
                seen.setdefault(row.key, None)
    return list(seen)
