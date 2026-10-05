"""
Shekel Budget App -- What the reconcile panel NAMES before a press

The values for what a tick would free that the page states and posts back
(plan step ``credit_card:CC-5-4a-5``, leaf 5c-2c-1, ruling **R-CC135**,
developer 2026-10-04, "One check per save"): a match the panel names under
each of the rows it needs (:class:`SharedMatch`), what each such warning
posts back (:class:`SharedShown`), and the whole of what the page named,
graded against the rows ticked (:class:`NamedLines`).  Split from
:mod:`._offers` by subject when that module reached this project's
1,000-line bound (ruling **balance:R-IR**): the offers say what a tick IS,
and this says what the page TOLD the owner it would free, which the save's
one press is checked against (``_assemble.record_reconciliation``).

Architecture (``CLAUDE.md``):
  - No Flask imports.  Frozen dataclasses, no behaviour beyond
    :meth:`NamedLines.for_ticks`.
"""

from dataclasses import dataclass, field
from typing import ClassVar

from app.services.match_withdrawal import MatchWithdrawal
from app.services.pay_calendar import DerivedPeriod


@dataclass(frozen=True)
class SharedPartner:
    """Another row a shared match needs closed, as its warning names it.

    Attributes:
        name: The row's name.
        period: The pay period it is budgeted in, printed beside the name
            only where :attr:`needs_period` says the name alone does not say
            which row it is.
        needs_period: Whether another row on the same list carries the same
            name -- one envelope in two paychecks.
    """

    name: str
    period: DerivedPeriod
    needs_period: bool


@dataclass(frozen=True)
class SharedMatch:
    """One accepted match a tick withdraws only with other rows ticked beside it.

    Plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2c-1), ruling **R-CC135**
    (developer 2026-10-04, "One check per save"): *"Warnings name each match
    under every row it names, saying which rows must all close: 'Matched with
    Dining to one bank line: closing both from their purchases withdraws that
    match, so 9/24 PAYMENT -$205.00 is unexplained again on your statement
    screen.'"*  Printed under each row it needs, and posted back from each as
    its bank lines WITH those rows (:attr:`FIELD`): the save counts the lines
    as named only when every one of the rows is ticked
    (``_assemble.record_reconciliation``), so one of them ticked alone saves
    with the match kept on the others (*"Tick Groceries only: saved, the
    match stays on Dining's $85.00"*), and all of them ticked save with the
    line unexplained (finding **CC-384**).

    Attributes:
        withdrawal: What withdrawing the match frees -- one act
            (``match_withdrawal.SharedWithdrawal``).
        partners: The OTHER rows it needs, in the order the arm's reader
            loads them.
        row_ids: Every row it needs, this one included: what the page posts
            back beside the lines.
    """

    #: The form field each shared match posts ``"<line ids>;<row ids>"``
    #: under, and the separator between the halves -- each spelled once for
    #: the template and the route.
    FIELD: ClassVar[str] = "shared_lines"
    SEPARATOR: ClassVar[str] = ";"

    withdrawal: MatchWithdrawal
    partners: "tuple[SharedPartner, ...]"
    row_ids: "frozenset[int]"


@dataclass(frozen=True)
class SharedShown:
    """One shared match's posted value: the lines a page named, and the rows they need.

    What :attr:`SharedMatch.FIELD` carries back (leaf 5c-2c-1, ruling
    **R-CC135**).  Owner input and never a scope: the save's press only
    compares the lines, and only when every one of :attr:`row_ids` was
    ticked.

    Attributes:
        line_ids: The bank lines the warning named.
        row_ids: The rows it said must all close.
    """

    line_ids: "frozenset[int]"
    row_ids: "frozenset[int]"


@dataclass(frozen=True)
class NamedLines:
    """What the reconcile panel's captions NAMED, as the page posts them back.

    Plan step ``credit_card:CC-5-4a-5``, rulings **R-CC76** / **R-CC127**:
    a tick that would take a matched payment out of its matches names the
    bank lines it frees before the press, and the page posts them back; and
    ruling **R-CC135** (developer 2026-10-04, "One check per save", leaf
    5c-2c-1): a match naming several rows' payments is named under each of
    them with the rows that must all close (:class:`SharedMatch`).

    Attributes:
        under_rows: ``{transaction id: bank line ids}`` -- what the caption
            under each row's tick named
            (:attr:`~._offers.TickForm.shown_prefix`), the matches that tick
            empties by itself.
        shared: What each shared match's warning named (:class:`SharedShown`,
            :attr:`SharedMatch.FIELD`), its lines with the rows they need.
    """

    under_rows: "dict[int, frozenset[int]]" = field(default_factory=dict)
    shared: "tuple[SharedShown, ...]" = ()

    def for_ticks(self, ticked: "set[int]") -> "frozenset[int]":
        """Return every bank line the page named for the rows TICKED.

        The lines captioned under each ticked row, and the lines of each
        shared match whose rows were ALL ticked (ruling **R-CC135**: *"Tick
        both: saved, ... the line unexplained.  Tick Groceries only: saved,
        the match stays on Dining's $85.00."*).  A shared match with one of
        its rows unticked adds nothing, so a save that keeps the match
        standing (it frees none of its lines) is graded equal; one whose rows
        are all ticked adds its lines, so a save that does not free them --
        another tab already did, or a row's save went another way -- is
        refused as out of date (finding **CC-384**; ledger row **BAL-597**).

        Args:
            ticked: The row ids the owner ticked.

        Returns:
            The line ids the save's one press is graded against.
        """
        return frozenset().union(
            *(self.under_rows.get(row_id, frozenset()) for row_id in ticked),
            *(
                shared.line_ids for shared in self.shared
                if shared.row_ids <= ticked
            ),
        )
