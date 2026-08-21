"""What SHAPE a transaction row is, and what that shape FORBIDS.

**Split out of :mod:`._settle` at plan step X-au-j**, whose CC-payback refusal
pushed that module past ``max-module-lines``.  The cut is by what each function
DECIDES rather than by size, which is this package's own rule (see the package
docstring), and it is the same seam :mod:`app.services.status_seam._refusals`
was cut on at plan step X-au-c3 -- *"they are gathered here because they are
one subject"*.  Nothing here settles anything, resolves an amount or touches
the session.  Each answers
one question about a row that constrains what a door may then do with it.

**Shaving prose to stay under the cap was the alternative and this package has
already ruled against it**: *"three lines of headroom is not a design, and the
structural answer is a package with one private leaf per verb"*.  A first
attempt at this step trimmed docstrings to 999 lines before taking that
sentence at its word.

Two of the three are consumed by ROUTES, which is why they are published on the
package: a screen decides whether to render an amount box from
:func:`settles_from_entries` and :func:`repays_card_spend`, and the PATCH
handler refuses a figure submitted against either anyway.  The third,
:func:`reject_unsettleable`, is the settle doors' shared guard.

Boundary discipline (``CLAUDE.md`` Architecture): ORM rows in, a bool or a
raise out; no Flask import, no writes.
"""

from app.exceptions import ValidationError
from app.models.transaction import Transaction


def settles_from_entries(txn: Transaction) -> bool:
    """Return whether a settle DERIVES this row's amount from its entries.

    **The verb's own branch predicate, published** -- because three things ask
    it and NONE of them is this module (they were two of three until plan step
    X-au-j moved this function out of :mod:`._settle`).
    :func:`~._settle.settle_transaction` picks its branch on it;
    :func:`~._settle.settle_amount` values the row on it; and the reconcile
    panel decides whether to render an editable amount on it, which is ruling
    **R-FF**: a tick is correctable exactly when the verb takes its MANUAL
    branch.

    Writing the predicate at each of those three sites is the shape this arc
    exists to remove, and the failure it produces here is specific: a panel
    that offers a box the verb ignores takes a user's typed figure and drops
    it, silently, on the screen whose whole job is entering the true one.

    **Both halves are load-bearing.**  ``tracks_purchases`` alone would claim
    production's ``Kayla's Spending Money`` -- envelope-tracked, `$100.00`
    budgeted, ZERO entries -- derives its amount from entries that do not
    exist, settling it at `$0.00` and refusing the user the box that would have
    corrected it.

    Args:
        txn: The row.  Reads ``tracks_purchases`` (a template lookup for a
            template-linked row) and the ``entries`` relationship.

    Returns:
        True when a settle takes the ``sum(entries)`` branch.
    """
    return bool(txn.tracks_purchases and txn.entries)


def repays_card_spend(txn: Transaction) -> bool:
    """Return whether *txn* is a CC PAYBACK, whose figure is not its own to state.

    **The sibling of :func:`settles_from_entries` one column over**, published
    for the same reason: THREE surfaces ask it and none is this module -- the
    full-edit popover and the inline quick-edit each decide whether to render
    an Estimated box, and the PATCH handler behind both refuses a figure
    submitted for one anyway.  (A first census said two and missed the
    quick-edit route, which is live under ``@login_required`` even though
    nothing links to it any more.)

    A payback repays the card spend of the row it names, so its figure is a
    fact about THAT row.  Both kinds make a typed one a lie, by OPPOSITE
    mechanisms: an ENTRY-backed payback is re-stated as the source's credit sum
    on every entry mutation (``entry_credit_workflow.sync_entry_payback``), so
    the typed figure is silently reverted -- finding **N-252**, ``$58.40`` on
    payback 2590, edited to ``$123.18`` against ``$181.58`` and settled there;
    a ROW-backed one is copied once by
    ``credit_workflow.create_cc_payback_transaction`` and repaired never, so it
    STICKS and quietly stops matching the card.  ``is_override`` cannot carry
    either case -- ``mutations._apply_field_updates`` sets it only for a
    TEMPLATE-linked row, and a payback carries no template and no transfer.

    Args:
        txn: The row.  Reads ``credit_payback_for_id`` only -- the LINK is what
            makes a row a payback, and no relationship is loaded, so a caller
            rendering a grid pays nothing for asking.

    Returns:
        ``True`` when the row is a CC payback.
    """
    return txn.credit_payback_for_id is not None


def repays_tracked_purchases(txn: Transaction) -> bool:
    """Return whether a payback's figure is PURCHASES rather than a whole row.

    **Which REPAIR a refused hand edit should name**, and it is a real fork
    rather than a wording choice: the two payback kinds are corrected by
    different acts, and an adversarial design review 2026-08-20 found the
    refusal telling both of them to do the one that only works for the first.

    * an ENTRY-backed payback repays the ``is_credit`` purchases on an
      envelope, so it is corrected by editing THOSE purchases -- the act is on
      the source row and the payback follows through
      ``entry_credit_workflow.sync_entry_payback``;
    * a ROW-backed payback repays a whole transaction marked Credit, and that
      source has no purchases at all.  Its figure was COPIED at the mark
      (``credit_workflow.create_cc_payback_transaction``) and the source is
      immutable while it stays Credit, so the only repair is *Undo CC* on the
      source (``transactions.unmark_credit``, which DELETES the payback),
      correct the source, then mark it Credit again.

    Telling a row-backed payback's owner to "change the purchases it repays"
    names an act with no target, which turns a silently wrong figure into a
    permanently wrong one -- the failure the refusal exists to prevent, with
    the repair removed.

    **It loads the source relationship where :func:`repays_card_spend` reads
    only a column**, which is why they are two functions rather than one: the
    cheap one is asked of every row a grid renders, and this is asked once, of
    a single row, when its own popover is being built.

    Args:
        txn: A payback -- a row :func:`repays_card_spend` answered ``True``
            for.  Reads ``credit_payback_for`` and, through it,
            ``tracks_purchases``.

    Returns:
        ``True`` when the source keeps its spend in purchases.  ``False`` for a
        single-spend source AND for a payback whose source cannot be loaded at
        all: the FK is ``ondelete="SET NULL"``, so an orphan is expressible,
        and the whole-row repair is the honest instruction for one -- there are
        no purchases to send anybody to.
    """
    source = txn.credit_payback_for
    return source is not None and source.tracks_purchases


def reject_unsettleable(txn: Transaction) -> None:
    """Refuse a row NO settle door may touch -- both rules, stated once.

    **Two refusals in one statement, because they are the same kind of rule and
    they had drifted apart** (finding **N-233**).  Every public settle surface
    asks it, and since plan step X-au-j they all live NEXT DOOR in
    :mod:`._settle` rather than beside it: :func:`~._settle.settle_transaction`,
    which would otherwise settle one leg of a transfer pair silently;
    :func:`~._settle.settle_amount`, which would otherwise price one off the
    loan-payment seam and hand a caller a figure that module refuses to book;
    and :func:`~._settle.settle_from_entries`, which
    asked BOTH questions in its own words and so gave the transfer rule a
    second, shorter spelling.  A verb owns its own preconditions; three verbs
    owning the same two own them once.

    **A transfer shadow** settles through ``transfer_service.update_transfer``
    so both legs and the parent move together (``CLAUDE.md`` transfer invariants
    3 and 4).

    **A soft-deleted row** must not be resurrected by a status change.  It
    values at ``Decimal("0")`` through the valuation gate, so settling one
    books nothing while stamping the row Paid and dated: a row that reads
    settled and is worth nothing.  The envelope branch refused this from the
    beginning and the MANUAL branch never did, and the gap was REACHABLE --
    ``get_accessible_transaction`` does not filter ``is_deleted``, so
    ``POST /transactions/<id>/mark-done`` on a soft-deleted non-envelope row
    flipped it into the settled band.  Measured on production: 102 soft-deleted
    rows, every one of them Projected, so the ledger cost is ``$0.00`` and the
    cost is to the data.

    Ordered shadow-then-deleted so a row that is both reports the rule that
    routes it somewhere else rather than the one that refuses it outright.  Both
    are column reads, so neither triggers the relationship lazy-load
    :func:`settles_from_entries`' cheap-first precondition ordering avoids.

    Args:
        txn: The row to check.  Reads ``transfer_id`` and ``is_deleted``.

    Raises:
        ValidationError: When *txn* is a transfer shadow or is soft-deleted.
    """
    if txn.transfer_id is not None:
        raise ValidationError(
            f"Transaction {txn.id} is a transfer shadow; "
            "transfers settle via transfer_service.update_transfer so both "
            "legs and the parent move together.",
        )
    if txn.is_deleted:
        raise ValidationError(
            f"Transaction {txn.id} is soft-deleted; a settle cannot "
            "resurrect a deleted row.",
        )
