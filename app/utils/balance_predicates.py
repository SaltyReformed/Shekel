"""Centralized balance-contributing status predicate (E-15, MED-02).

MED-02 / D6-09 identified that the conceptual predicate "is this
transaction's ``effective_amount`` contributing to a projected balance"
is hand-reproduced in 20+ sites across the codebase in three structurally
different forms:

- Python in-loop skips, e.g. ``if txn.status_id != projected_id``
  (``balance_calculator.py`` three sites, ``grid.py``, ``credit_workflow.py``).
- SQLAlchemy filters, e.g. ``Status.excludes_from_balance.is_(False)``
  reproduced across ``year_end_summary_service.py``,
  ``savings_dashboard_service.py``, ``loan_payment_service.py``.
- Jinja conditionals against status IDs across the grid templates.

In addition the ``[CREDIT, CANCELLED]`` exclusion set is re-derived twice
under different names (``_get_excluded_status_ids`` in
``year_end_summary_service.py``; ``excluded_status_ids`` in
``budget_variance_service.py``) plus reproduced inline.

Every site is individually ID-based and correct today (E-15 is satisfied
at each site -- never a name-string comparison), so no displayed number
is wrong now. The risk MED-02 flags is that a one-sided change to the
status-to-balance rule -- adding a new exclusion, or relaxing one --
would silently drift a balance because the rule lives in N places, not
one.

This module is the single source of truth for the predicate. It exposes:

- ``is_balance_contributing(txn) -> bool`` mirrors exactly
  ``Transaction.effective_amount``'s gate: soft-deleted contributes
  zero, ``excludes_from_balance`` statuses contribute zero, everything
  else contributes its effective amount. This is the Python predicate
  for in-memory iteration.
- ``is_projected(txn) -> bool`` is the equality form ("is this txn
  Projected?") used by the inline ``!= projected_id`` / ``== projected_id``
  sites. Pure status equality; does not consider ``is_deleted`` (callers
  that need the combined gate use ``is_balance_contributing``).
- ``balance_excluded_status_ids() -> frozenset[int]`` is the cached
  ``{Credit.id, Cancelled.id}`` set, derived from the same ``ref_cache``
  lookups as the clause builder so they can never disagree.
- ``balance_contributing_clause()`` is the SQLAlchemy boolean expression
  for ORM queries. It uses ``Transaction.status_id`` directly so callers
  do not need to ``.join(Status)``: the ID set comes from the same
  ``balance_excluded_status_ids()`` accessor the Python predicate
  consults, so the SQL filter and the Python loop classify any
  transaction identically.

Commits 5 and 10 of the financial-calculation remediation route the
canonical balance and period-subtotal producers through these helpers;
Commit 29 finishes routing the residual inline Python skips, the
remaining SQLAlchemy filters, and the Jinja-template predicates through
them, and extends this module with the per-status equality predicates
and SQL clause builders the residual sites consume:

- ``is_projected``, ``is_credit``, ``is_cancelled``, ``is_done`` --
  Python equality predicates for the four statuses the call sites
  branch on. ``is_projected`` was introduced in Commit 2; Commit 29
  adds the rest so every per-status equality check in business logic
  routes through one cached-ID source.
- ``is_projected_clause(model_class)`` -- the SQL form of
  ``is_projected``, parameterised on the model class so both
  ``Transaction`` queries (dashboard, entries, carry-forward,
  templates) and ``Transfer`` queries (transfer-template archive /
  unarchive / hard-delete) share one definition.

Per E-15 / CLAUDE.md rule 4 ("IDs for logic, strings for display only"),
every predicate in this module is implemented over the semantic boolean
columns on the status row (``excludes_from_balance``) or over cached
integer IDs from ``ref_cache``. The status display string is never
consulted; the C2-8 test asserts this property mechanically against the
module source.
"""
from sqlalchemy import and_

from app import ref_cache
from app.enums import StatusEnum
from app.models.transaction import Transaction


def balance_excluded_status_ids() -> frozenset[int]:
    """Return the cached set of status IDs excluded from balance contribution.

    Per ``app/ref_seeds.py`` the rows with ``excludes_from_balance=True``
    are exactly ``Credit`` and ``Cancelled`` -- both represent a
    transaction whose dollar amount is settled elsewhere (paid by credit
    card, or cancelled outright) and therefore must contribute zero to
    the projected checking balance. ``Transaction.effective_amount``
    already encodes this exclusion; the SQLAlchemy clause builder
    consumes this same set so the in-Python predicate and the ORM filter
    cannot disagree.

    Returns:
        A ``frozenset[int]`` of the ``ref.statuses.id`` values for
        ``StatusEnum.CREDIT`` and ``StatusEnum.CANCELLED``. ``frozenset``
        (not ``set``) so the value is hashable and immutable -- callers
        treat it as an inert lookup, never mutate it.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized. The cache is
            populated by ``create_app()`` after seeding; production and
            test paths both initialize it before any service runs.
    """
    return frozenset({
        ref_cache.status_id(StatusEnum.CREDIT),
        ref_cache.status_id(StatusEnum.CANCELLED),
    })


def status_contributes_to_balance(txn) -> bool:
    """Return True iff *txn*'s status alone permits balance contribution.

    The status-only half of :func:`is_balance_contributing`: returns
    ``False`` for an ``excludes_from_balance=True`` status
    (``Credit``, ``Cancelled``) and ``True`` for every other status,
    *without* consulting ``txn.is_deleted``.  Sized for callers that
    have already pre-filtered deleted rows upstream (the
    investment-projection Python iteration sites consume already-
    SQL-filtered shadow contribution transactions) and whose duck-
    typed test fakes therefore do not carry an ``is_deleted``
    attribute.

    ``is_balance_contributing`` is defined as
    ``not txn.is_deleted and status_contributes_to_balance(txn)`` so
    the two predicates can never disagree about the status-only half
    of the rule.

    Args:
        txn: any object with a ``status`` attribute that, when not
            ``None``, carries an ``excludes_from_balance`` boolean.
            ``Transaction`` and the ``FakeContribTransaction`` test
            duck-types both satisfy this; ``is_deleted`` is NOT
            consulted.

    Returns:
        ``True`` if the status row's ``excludes_from_balance`` is
        ``False`` (or the status is ``None`` -- treated as
        contributing, matching the
        :attr:`Transaction.effective_amount` fallback); ``False`` if
        the status carries ``excludes_from_balance=True``.
    """
    return not (txn.status is not None and txn.status.excludes_from_balance)


def is_balance_contributing(txn: Transaction) -> bool:
    """Return True iff *txn* contributes its effective amount to a balance.

    Mirrors the gate in ``Transaction.effective_amount`` exactly: a
    soft-deleted transaction contributes zero, a transaction whose
    status has ``excludes_from_balance=True`` (``Credit``, ``Cancelled``)
    contributes zero, everything else contributes its effective amount.
    The two predicates share one definition so the in-Python balance
    loop and any consumer that wants to ask "should this row's amount
    be summed" cannot drift apart from the ``effective_amount`` rule.

    Args:
        txn: a ``Transaction`` instance. Both ``is_deleted`` and the
            ``status`` relationship are expected to be loaded;
            ``Transaction.status`` is declared ``lazy="joined"`` so a
            standard ORM load satisfies this without explicit
            ``selectinload``.

    Returns:
        ``True`` if the transaction's ``effective_amount`` would be a
        non-excluded value (i.e. it participates in balance projection);
        ``False`` if either soft-deleted or carrying an
        ``excludes_from_balance`` status.

    Note:
        A ``txn`` with ``status is None`` is treated as contributing.
        This matches ``Transaction.effective_amount``, which guards the
        exclusion behind ``if self.status and ...``: an unloaded or
        in-construction status is not evidence of exclusion, so the
        predicate defers to ``effective_amount``'s own fallback
        behavior. Callers that need to assert a fully-loaded status
        should do so at their own boundary.
    """
    if txn.is_deleted:
        return False
    return status_contributes_to_balance(txn)


def is_projected(txn: Transaction) -> bool:
    """Return True iff *txn*'s status is ``Projected``.

    Centralizes the inline ``status_id != ref_cache.status_id(
    StatusEnum.PROJECTED)`` and ``status_id == projected_id`` comparisons
    that recur across ``balance_calculator.py``, ``grid.py``, and
    ``credit_workflow.py``. The comparison is pure status equality and
    does not consider ``is_deleted`` -- callers that need the combined
    "live and balance-contributing" gate compose this predicate with
    ``is_balance_contributing``, or use ``is_balance_contributing``
    alone when they only need the exclusion set semantics.

    Args:
        txn: a ``Transaction`` instance with ``status_id`` populated.

    Returns:
        ``True`` if ``txn.status_id`` equals the cached integer ID for
        ``StatusEnum.PROJECTED``; ``False`` for every other status,
        including ``Paid``, ``Received``, ``Credit``, ``Cancelled``,
        and ``Settled``.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)


def is_credit(txn: Transaction) -> bool:
    """Return True iff *txn*'s status is ``Credit``.

    Centralizes the inline ``status_id == credit_id`` /
    ``status_id != credit_id`` comparisons in
    ``credit_workflow.py`` (mark-as-credit idempotency check;
    unmark-credit precondition guard) and ``entry_service.py``
    (block entries on credit-status transactions). Pure status
    equality, does not consider ``is_deleted``.

    Args:
        txn: a ``Transaction`` instance with ``status_id`` populated.

    Returns:
        ``True`` if ``txn.status_id`` equals the cached integer ID for
        ``StatusEnum.CREDIT``; ``False`` for every other status.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return txn.status_id == ref_cache.status_id(StatusEnum.CREDIT)


def is_cancelled(txn: Transaction) -> bool:
    """Return True iff *txn*'s status is ``Cancelled``.

    Centralizes the inline ``status_id == cancelled_id`` comparisons
    in ``app/routes/grid.py`` (skip-cancelled row-key collection,
    mirroring the templates' ``!= STATUS_CANCELLED`` guards in
    ``grid.html``, ``_mobile_grid.html``) and ``entry_service.py``
    (block entries on cancelled-status transactions). Pure status
    equality, does not consider ``is_deleted``.

    Note that this predicate is intentionally narrower than
    ``is_balance_contributing``: a ``Credit`` transaction is excluded
    from balance contribution but is NOT cancelled, and the grid
    still renders the Credit row (with strike-through styling)
    whereas a Cancelled row is omitted from the row-key set.

    Args:
        txn: a ``Transaction`` instance with ``status_id`` populated.

    Returns:
        ``True`` if ``txn.status_id`` equals the cached integer ID for
        ``StatusEnum.CANCELLED``; ``False`` for every other status.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)


def is_done(txn: Transaction) -> bool:
    """Return True iff *txn*'s status is ``Paid`` (``StatusEnum.DONE``).

    Centralizes the inline ``status_id == done_id`` comparison in
    ``entry_service._update_actual_if_paid`` (recompute
    ``actual_amount`` from entries when the parent transaction is
    already Paid). Pure status equality, does not consider
    ``is_deleted``.

    Note on the name: ``StatusEnum.DONE`` is the enum member; the
    ref-table row carries display name "Paid" and ``is_settled=True``.
    The predicate is named ``is_done`` to match the enum identifier
    so a future renaming of the enum surfaces in this helper as well.

    Args:
        txn: a ``Transaction`` instance with ``status_id`` populated.

    Returns:
        ``True`` if ``txn.status_id`` equals the cached integer ID for
        ``StatusEnum.DONE``; ``False`` for every other status.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return txn.status_id == ref_cache.status_id(StatusEnum.DONE)


def is_projected_clause(model_class):
    """Return a SQLAlchemy boolean clause matching ``Projected``.

    Centralizes the eleven SQLAlchemy filter sites that previously
    each bound ``projected_id = ref_cache.status_id(StatusEnum.
    PROJECTED)`` locally and wrote ``Model.status_id == projected_id``
    inline (D6-09 (ii); five in ``app/routes`` for the template /
    transfer archive workflow and the entries auto-clear, six in
    ``app/services`` for the dashboard / entries / carry-forward
    queries). After this commit those sites all read
    ``is_projected_clause(Transaction)`` /
    ``is_projected_clause(Transfer)`` so the rule "what does a
    Projected filter look like in SQL" is defined once.

    The clause is intentionally polymorphic over the model class
    because ``Transaction`` and ``Transfer`` both carry an FK named
    ``status_id`` against ``ref.statuses.id`` and the D6-09 (ii)
    register covers both. Other models with a different status
    column shape would need their own helper; passing one in here
    is a usage error caught by the missing-attribute ``AttributeError``
    at filter-build time.

    Args:
        model_class: ``app.models.transaction.Transaction`` or
            ``app.models.transfer.Transfer``. Any other class with a
            ``status_id`` column attribute also works (the helper is
            structurally typed) but the two listed are the only
            current callers.

    Returns:
        A SQLAlchemy boolean expression equivalent to
        ``model_class.status_id == <PROJECTED.id>`` suitable for
        ``query.filter(...)``.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return model_class.status_id == ref_cache.status_id(StatusEnum.PROJECTED)


def balance_contributing_clause():
    """Return a SQLAlchemy boolean clause matching ``is_balance_contributing``.

    The Python predicate and this ORM filter are generated from the
    same ``ref_cache``-backed accessors (``balance_excluded_status_ids``)
    so they classify any transaction identically -- the C2-6 parity
    test enforces this on a mixed-status seeded set. Callers compose
    the clause into any query over ``Transaction`` without needing to
    ``.join(Status)``: ``Transaction.status_id`` is the discriminator,
    and the excluded-ID set is the cached lookup.

    Returns:
        A SQLAlchemy ``and_`` clause equivalent to
        ``Transaction.is_deleted IS FALSE AND
        Transaction.status_id NOT IN (Credit.id, Cancelled.id)``.
        Suitable for ``query.filter(balance_contributing_clause())``
        on any select rooted at ``Transaction``.

    Raises:
        RuntimeError: propagated from ``balance_excluded_status_ids``
            if the reference cache has not been initialized.
    """
    return and_(
        Transaction.is_deleted.is_(False),
        Transaction.status_id.notin_(balance_excluded_status_ids()),
    )
