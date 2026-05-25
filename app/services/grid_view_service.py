"""
Shekel Budget App -- Grid View Service

Pure template-data producer for the budget grid.  Builds the sorted
row-key sequence (one entry per logical line item: a template-linked
group or a standalone name) and the
``(category_id, template_id, txn_name, period_id) -> [Transaction]``
matching dict that drives every cell render.

Single source of truth shared by:

  * the owner-facing grid (``app/routes/grid.py::index``), which
    consumes the dict from the desktop ``render_row_cells`` and the
    mobile ``render_row_card`` macros in
    ``app/templates/grid/_grid_row_macros.html``;
  * the companion view (``app/routes/companion.py::index``), which
    renders the shared ``grid/_mobile_this_period.html`` partial via
    the same macros (mobile-first v3 plan Commit 13 / D-B).

Architecture (per CLAUDE.md "Architecture" and
``docs/coding-standards.md``):

  * No Flask imports.  Takes plain SQLAlchemy collections and the
    ``RowKey`` shape; returns plain ``list[RowKey]`` and ``dict``.
  * No monetary arithmetic.  Status / category / income-vs-expense
    filtering only; every displayed dollar amount still flows through
    ``balance_resolver`` per mobile-first v3 plan Section 1 rule 2.
  * Cancelled-status filtering is routed through
    :func:`app.utils.balance_predicates.is_cancelled` so the Python
    producer and the Jinja templates' Cancelled-status guards share
    one cached-ID definition (E-15 / MED-02).
"""

from collections import namedtuple
from collections.abc import Iterable


from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.utils.balance_predicates import is_cancelled


# Lightweight struct for a single row in the budget grid.  Template-
# linked transactions collapse to one row per (category, template)
# regardless of per-instance name drift; standalone transactions
# collapse to one row per (category, name).
RowKey = namedtuple("RowKey", [
    "category_id",    # int -- FK to budget.categories
    "template_id",    # int or None -- FK to budget.transaction_templates
    "txn_name",       # str -- row label (template name or standalone txn name)
    "group_name",     # str -- category group for section headers
    "item_name",      # str -- category item (used for sort tiebreaker)
    "display_name",   # str -- label shown in the row <th>
    "category",       # Category -- full ORM object for empty-cell rendering
])


def _short_display_name(name: str) -> str:
    """Strip redundant prefixes from transaction names for row headers.

    Transfer shadows are named "Transfer to X" / "Transfer from X" and
    credit paybacks "CC Payback: X".  The grid cell already shows a
    transfer icon or CC badge, so the prefix is visual noise in the
    row label.  Strip it to show only the meaningful part.
    """
    lower = name.lower()
    if lower.startswith("transfer to "):
        return name[len("Transfer to "):]
    if lower.startswith("transfer from "):
        return name[len("Transfer from "):]
    if lower.startswith("cc payback: "):
        return name[len("CC Payback: "):]
    return name


def build_row_keys(
    transactions: Iterable[Transaction],
    categories: Iterable[Category],
    is_income_section: bool,
) -> list[RowKey]:
    """Build a deterministic, sorted list of RowKeys for the grid.

    Scans the supplied transactions and collects one row per logical
    line item.  Template-linked transactions dedupe by
    (category_id, template_id) and take their label from the current
    template name -- this keeps historic instances whose stored ``name``
    predates a template rename from splitting into a second row.
    Standalone transactions (no template_id) dedupe by
    (category_id, name) and label themselves with the instance name.
    Results are sorted by (group_name, item_name, txn_name) for stable
    alphabetical ordering within each category group.

    The caller controls scope: passing only visible-window transactions
    produces the default compact view (rows only for items active in
    the visible periods), while passing the full projection yields the
    show-all view.  Either way, cell matching in the template is
    unaffected -- it still walks ``txn_by_period`` per visible period.

    Args:
        transactions: iterable of Transaction objects to consider for
            row-key generation.  Transactions with a non-null
            ``template_id`` must have their ``template`` relationship
            loaded (the grid route does this via ``selectinload``;
            the companion route does it via the join in
            ``companion_service.get_visible_transactions``) to avoid
            per-row lazy fetches.
        categories: list of Category objects, already ordered by
            (group_name, item_name).  Used to map category_id -> Category
            for sort keys and for the empty-cell template.
        is_income_section: bool -- True to collect income transactions,
            False for expense transactions.

    Returns:
        list[RowKey] -- one entry per unique transaction row, sorted by
        (group_name, item_name, txn_name).  Deterministic across calls
        with the same data.
    """
    # Index categories by ID for O(1) lookup.
    cat_by_id = {c.id: c for c in categories}

    # Collect unique row keys.  For template-linked rows the key
    # carries template_id (name omitted); for standalone rows the key
    # carries the instance name (template_id omitted).
    seen = set()
    row_keys: list[RowKey] = []

    for txn in transactions:
        # Skip deleted and cancelled transactions.  Routed through
        # the centralized ``is_cancelled`` predicate (D6-09 /
        # MED-02) so the Python row-key collector and the Jinja
        # ``!= STATUS_CANCELLED`` row guards in ``grid.html`` /
        # ``_mobile_grid.html`` share one definition of the rule.
        if txn.is_deleted or is_cancelled(txn):
            continue

        # Filter by income/expense.
        if is_income_section and not txn.is_income:
            continue
        if not is_income_section and not txn.is_expense:
            continue

        # Look up the category.  Transactions may have category_id=NULL
        # (e.g. transfer shadow transactions when the user's default
        # "Transfers: Incoming/Outgoing" categories are missing).
        # These must still appear in the grid -- use a fallback group.
        cat = cat_by_id.get(txn.category_id)
        group_name = cat.group_name if cat else "Uncategorized"
        item_name = cat.item_name if cat else ""

        if txn.template_id is not None:
            # Template-linked: collapse all instances into one row
            # labelled with the template's current name.  Falls back
            # to the instance name only if the relationship failed
            # to load (template.ondelete=SET NULL makes a real
            # orphan unreachable through template_id).
            label = txn.template.name if txn.template else txn.name
            key = (txn.category_id, txn.template_id, None)
        else:
            label = txn.name
            key = (txn.category_id, None, txn.name)

        if key not in seen:
            seen.add(key)
            row_keys.append(RowKey(
                category_id=txn.category_id,
                template_id=txn.template_id,
                txn_name=label,
                group_name=group_name,
                item_name=item_name,
                display_name=_short_display_name(label),
                category=cat,
            ))

    # Sort by (group_name, item_name, txn_name) for deterministic ordering.
    row_keys.sort(key=lambda rk: (rk.group_name, rk.item_name, rk.txn_name))

    return row_keys


def _match_row_in_period(
    rk: RowKey,
    period: PayPeriod,
    txn_by_period: dict[int, list[Transaction]],
    is_income_section: bool,
) -> list[Transaction]:
    """Return the transactions matching ``rk`` in ``period``.

    Inner half of :func:`build_matched_by_row_period`, lifted to its
    own function so the outer cross-product loop stays flat (pylint
    ``too-many-nested-blocks``) and so the per-cell predicate is
    individually testable without instantiating the full row-key set.
    See :func:`build_matched_by_row_period` for the predicate
    semantics.
    """
    matched: list[Transaction] = []
    for txn in txn_by_period.get(period.id, []):
        if txn.category_id != rk.category_id:
            continue
        if is_income_section and not txn.is_income:
            continue
        if not is_income_section and not txn.is_expense:
            continue
        if txn.is_deleted or is_cancelled(txn):
            continue
        if rk.template_id is not None and txn.template_id is not None:
            if txn.template_id != rk.template_id:
                continue
        elif txn.name != rk.txn_name:
            continue
        matched.append(txn)
    return matched


def build_matched_by_row_period(
    income_row_keys: list[RowKey],
    expense_row_keys: list[RowKey],
    periods: Iterable[PayPeriod],
    transactions: Iterable[Transaction],
) -> dict[tuple[int, int | None, str, int], list[Transaction]]:
    """Pre-compute the (row_key, period) -> matched transactions dict.

    Single source of truth for the grid's matching predicate: for each
    row key and each visible period, find the transactions that belong
    in that cell.  The Jinja grid templates (``grid.html`` and
    ``_mobile_grid.html``) previously hand-rolled this match in four
    duplicated blocks; the macros introduced in mobile-first v3 plan
    Commit 1 and the templates wired in Commits 3 and 4 consume this
    dict instead, so the predicate is defined once.

    Predicate (mirrors the Jinja loops at ``grid.html`` lines 158-173
    and 234-246 text-for-text):

    1. ``txn.category_id == rk.category_id``.
    2. Income section -> ``txn.is_income``; expense section ->
       ``txn.is_expense``.  Row keys are already partitioned by
       income/expense at ``build_row_keys`` time, but this redundant
       per-txn guard preserves the Jinja predicate verbatim.
    3. ``not txn.is_deleted``.
    4. ``not is_cancelled(txn)`` -- routed through the centralized
       ``is_cancelled`` helper so the Python producer and the Jinja
       templates' Cancelled-status guard share the same cached-ID
       source per E-15 / MED-02.
    5. If both the row key and the txn carry a ``template_id``, match
       by template id.  Otherwise fall back to name match.

    Args:
        income_row_keys: row keys for the income section, in
            row-render order.
        expense_row_keys: row keys for the expense section, in
            row-render order.
        periods: iterable of PayPeriod objects -- the visible cells
            to render.
        transactions: iterable of Transaction objects (already filtered
            for user / account / scenario / soft-delete).  The function
            indexes these by ``pay_period_id`` internally so the caller
            does not need to pre-group.

    Returns:
        ``dict[(category_id, template_id, txn_name, period_id),
        list[Transaction]]``.  Keys are 4-tuples uniquely identifying
        the (row, period) cell; values are non-empty lists of
        Transaction ORM objects in insertion order.  Cells with no
        matched txns are omitted (the macro callers default to ``[]``
        via ``dict.get``).
    """
    # Group transactions by pay_period_id once so the inner predicate
    # only iterates the period-relevant subset.  Mirrors the
    # ``txn_by_period`` produced by the grid route in v1.
    txn_by_period: dict[int, list[Transaction]] = {}
    for txn in transactions:
        txn_by_period.setdefault(txn.pay_period_id, []).append(txn)

    matched_by_row_period: dict[
        tuple[int, int | None, str, int], list[Transaction]
    ] = {}
    for row_keys, is_income_section in (
        (income_row_keys, True),
        (expense_row_keys, False),
    ):
        for rk in row_keys:
            for period in periods:
                matched = _match_row_in_period(
                    rk, period, txn_by_period, is_income_section,
                )
                if matched:
                    matched_by_row_period[
                        (rk.category_id, rk.template_id, rk.txn_name, period.id)
                    ] = matched
    return matched_by_row_period
