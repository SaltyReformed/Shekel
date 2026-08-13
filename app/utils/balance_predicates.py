"""Centralized balance-contributing status predicate (E-15, MED-02).

MED-02 / D6-09 identified that the conceptual predicate "is this
transaction's amount contributing to a projected balance"
is hand-reproduced in 20+ sites across the codebase in three structurally
different forms:

- Python in-loop skips, e.g. ``if txn.status_id != projected_id``
  (``cash_ledger._flows`` / ``cash_ledger._amounts``, ``grid.py``,
  ``credit_workflow.py``; the three former ``balance_calculator.py`` sites moved
  to the cash ledger leaf at plan step D1c and that module now has none).
- SQLAlchemy filters, e.g. ``Status.excludes_from_balance.is_(False)``
  reproduced across ``year_end_summary_service.py``,
  ``savings_dashboard_service.py``, ``loan_payment_service.py``.
- Jinja conditionals against status IDs across the grid templates.

In addition the ``[CREDIT, CANCELLED]`` exclusion set is re-derived under a
different name (``_get_excluded_status_ids`` in
``year_end_summary_service.py``) plus reproduced inline.

Every site is individually ID-based and correct today (E-15 is satisfied
at each site -- never a name-string comparison), so no displayed number
is wrong now. The risk MED-02 flags is that a one-sided change to the
status-to-balance rule -- adding a new exclusion, or relaxing one --
would silently drift a balance because the rule lives in N places, not
one.

This module is the single source of truth for the predicate. It exposes:

- ``is_balance_contributing(txn) -> bool`` mirrors exactly
  the valuation's gate (``row_valuation.fixed_contribution``): soft-deleted contributes
  zero, ``excludes_from_balance`` statuses contribute zero, everything
  else contributes its effective amount. This is the Python predicate
  for in-memory iteration.
- ``is_projected(row) -> bool`` is the equality form ("is this row
  Projected?") used by the inline ``!= projected_id`` / ``== projected_id``
  sites. Pure status equality; does not consider ``is_deleted`` (callers
  that need the combined gate use ``is_balance_contributing``). It takes a
  ``Transaction`` OR a ``Transfer``, for the same reason
  ``is_projected_clause`` below is parameterised on the model class: both
  tables carry a ``status_id`` into ``ref.statuses``, and "is this row still
  Projected" is ONE question. The row form was Transaction-only until plan
  step X-au-b asked it of a transfer (``cash_ledger._amount_source``), where
  the alternative was a second spelling of the same comparison.
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
from datetime import date

from sqlalchemy import and_

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import UndatedSettleError
from app.models.transaction import Transaction
from app.models.transfer import Transfer


def balance_excluded_status_ids() -> frozenset[int]:
    """Return the cached set of status IDs excluded from balance contribution.

    Per ``app/ref_seeds.py`` the rows with ``excludes_from_balance=True``
    are exactly ``Credit`` and ``Cancelled`` -- both represent a
    transaction whose dollar amount is settled elsewhere (paid by credit
    card, or cancelled outright) and therefore must contribute zero to
    the projected checking balance. The valuation
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


def settled_status_ids() -> frozenset[int]:
    """Return the cached set of status IDs that represent a settled transaction.

    Per ``app/ref_seeds.py`` the rows with ``is_settled=True`` are
    exactly ``Paid`` (``StatusEnum.DONE``), ``Received`` and ``Settled``
    -- the three statuses whose real-world money movement has completed.
    This is the ID-list counterpart of the semantic ``Status.is_settled``
    column: SQLAlchemy filters that need the set for a
    ``status_id.in_(...)`` clause consume this accessor, while the sibling
    sites that read ``txn.status.is_settled`` (the calendar, variance,
    savings-metric, and balance-calculator Python loops) consult the
    column directly. Both forms resolve to the same three rows by
    construction, so a "settled spending" total computed via the ID list
    can never disagree with one gated on the boolean column. The
    ``TestSettledStatusIds`` parity test derives the expected set from the
    ``is_settled`` column itself, so adding or removing a settled status
    in the seed matrix without updating this accessor fails that test.

    Returns:
        A ``frozenset[int]`` of the ``ref.statuses.id`` values for
        ``StatusEnum.DONE``, ``StatusEnum.RECEIVED`` and
        ``StatusEnum.SETTLED``. ``frozenset`` (not ``set``) so the value
        is hashable and immutable -- callers treat it as an inert lookup,
        never mutate it.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized. The cache is
            populated by ``create_app()`` after seeding; production and
            test paths both initialize it before any service runs.
    """
    return frozenset({
        ref_cache.status_id(StatusEnum.DONE),
        ref_cache.status_id(StatusEnum.RECEIVED),
        ref_cache.status_id(StatusEnum.SETTLED),
    })


def enters_settled_band(row, new_status_id: int) -> bool:
    """Return whether moving *row* to *new_status_id* SETTLES it.

    **The predicate that tells a status ASSIGNMENT from a SETTLE.**  Settling
    is entering the band from outside it: Projected -> Paid or Projected ->
    Received, the only two the state machine admits inward (Credit and
    Cancelled reach the band through Projected, never directly).

    **Staying inside the band is NOT a settle**, and that half is load-bearing.
    ``Paid -> Settled`` is an ARCHIVE of a row whose money already moved and
    whose amount is already a fact, and ``Paid -> Paid`` is an idempotent
    re-submit; routing either to a settle verb would ask an immutable row to
    re-price itself, which an envelope's settle refuses by precondition and a
    manual settle would answer by re-reading a projection the row left months
    ago.

    **It lives HERE rather than on either service, and the move is plan step
    X-f2-c3's.**  It was ``transaction_service``'s, published so
    ``apply_requested_status`` could dispatch on it; that leaf gave
    ``transfer_service.update_transfer`` the same dispatch -- a transfer's
    settle rule became structural there -- and a TRANSFER asking the
    transaction service "is this a settle" reads as a dependency that is not
    real.  The question is over ``status_id`` and :func:`settled_status_ids`
    and nothing else, which is this module's subject, and it is polymorphic in
    exactly the way :func:`is_projected_clause` already is: ``Transaction`` and
    ``Transfer`` both carry a ``status_id`` FK against ``ref.statuses.id``.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`, read for its CURRENT
            ``status_id``.
        new_status_id: The status a door is asking for.

    Returns:
        True when the move crosses INTO the settled band from outside it.
    """
    settled = settled_status_ids()
    return row.status_id not in settled and new_status_id in settled


def leaves_settled_band(row, new_status_id: int) -> bool:
    """Return whether moving *row* to *new_status_id* UNSETTLES it.

    :func:`enters_settled_band`'s mirror, and it exists for the same reason:
    the two directions are different acts and a door must not decide which is
    which.  The only edges out of the band are ``Paid -> Projected`` and
    ``Received -> Projected`` -- the documented unlock path, where the user is
    saying the money did not move after all.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`, read for its CURRENT
            ``status_id``.
        new_status_id: The status a door is asking for.

    Returns:
        True when the move crosses OUT of the settled band.
    """
    settled = settled_status_ids()
    return row.status_id in settled and new_status_id not in settled


def settled_day(transaction_id: int, settled_on: date | None) -> date:
    """Return the civil day a SETTLED transaction's money moved, or refuse.

    **The ONE accessor for "which day did this cash move", and it replaced
    ELEVEN derivations** (plan step X-f1, ruling R-EC).  Until then every
    consumer took ``transactions.paid_at`` -- the instant the user clicked --
    and converted it to a display-timezone civil day with the row's pay-period
    start as a fallback: the read fold, the posting walk's two loaders, the
    posting writer's two entry-date helpers, the loan payment writer, the loan
    fold's visibility rule, the confirmed-statement reader's two loaders, the
    loan interest tax-year basis, and ``Transaction.days_paid_before_due``.
    Fourteen sites over three helper layers, all computing one fact the row now
    stores.  Callers pass ``(id, row.settled_on)`` and get the day.

    It lives beside :func:`settled_status_ids` because it is that predicate's
    other half.  A row is settled if and only if it carries a settle day, and
    both facts are written by ONE statement -- ``status_seam.apply_status_change``
    is the single door that assigns ``status_id``, and it assigns
    ``settled_on`` in the same call.  That is why there is no CHECK constraint:
    the predicate lives in ``ref.statuses`` and a constraint cannot join, so the
    invariant is held structurally at the write door rather than declared at the
    storage tier.

    Its companion question -- "had this row's cash moved YET, at date D" -- is
    :func:`app.utils.dates.has_settled_by`, which lives one module over on a
    purity argument its callers force (see that function).  The two take
    opposite positions on a missing day, and each is right for its own caller.

    **A missing day is REFUSED, not defaulted, and that is the point of the
    function.**  The derivation this replaced fell back to the pay period's
    ``start_date`` whenever the instant was NULL, which was a guess the reader
    could not see; the day is now a stored fact, so its absence on a settled row
    means the invariant above is broken and no fallback is honest.  Silently
    dating such a row would put real money on a day nothing recorded, and
    silently DROPPING it would remove money from a balance without saying so.
    The condition is reachable rather than theoretical: a bulk
    ``query.update({status_id: paid})`` bypasses the ORM session entirely
    (finding N-65 measured 41 such sites in the suite), and a fixture that
    constructs ``Transaction(status_id=<paid>)`` directly never passes the seam.
    Both produce exactly this row, and both should fail where they are written.

    Args:
        transaction_id: The row's id, named in the refusal so a broken row is
            identifiable without re-querying.
        settled_on: The row's stored ``settled_on``, read either off the ORM
            attribute or out of a batched query tuple -- both shapes occur
            among the call sites, which is why this takes the VALUE rather than
            the row.

    Returns:
        The civil day the row's cash moved.

    Raises:
        UndatedSettleError: When *settled_on* is ``None``.  The caller is
            reading a row it believes is settled, so a missing day is a broken
            invariant rather than an empty value.
    """
    if settled_on is None:
        raise UndatedSettleError(
            f"Transaction {transaction_id} is in a settled status but carries "
            "no settled_on, so the day its money moved is unknown.  Every "
            "settled row is given one by status_seam.apply_status_change (the "
            "single status write door) and by migration a3f7c8e21b64's "
            "backfill, so this row was written by neither -- most likely a "
            "bulk query.update() on status_id, or a fixture constructing the "
            "row with a settled status directly.  Route the write through the "
            "seam; there is deliberately no fallback day, because inventing "
            "one would place real money on a day nothing recorded."
        )
    return settled_on


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
        valuation's own fallback); ``False`` if
        the status carries ``excludes_from_balance=True``.
    """
    return not (txn.status is not None and txn.status.excludes_from_balance)


def is_balance_contributing(txn: Transaction) -> bool:
    """Return True iff *txn* contributes its effective amount to a balance.

    Mirrors the gate in ``row_valuation.fixed_contribution`` exactly: a
    soft-deleted transaction contributes zero, a transaction whose
    status has ``excludes_from_balance=True`` (``Credit``, ``Cancelled``)
    contributes zero, everything else contributes its effective amount.
    The two predicates share one definition so the in-Python balance
    loop and any consumer that wants to ask "should this row's amount
    be summed" cannot drift apart from the valuation rule.

    Args:
        txn: a ``Transaction`` instance. Both ``is_deleted`` and the
            ``status`` relationship are expected to be loaded;
            ``Transaction.status`` is declared ``lazy="joined"`` so a
            standard ORM load satisfies this without explicit
            ``selectinload``.

    Returns:
        ``True`` if the transaction's contribution would be a
        non-excluded value (i.e. it participates in balance projection);
        ``False`` if either soft-deleted or carrying an
        ``excludes_from_balance`` status.

    Note:
        A ``txn`` with ``status is None`` is treated as contributing.
        This matches ``row_valuation.fixed_contribution``, which guards the
        exclusion behind ``if self.status and ...``: an unloaded or
        in-construction status is not evidence of exclusion, so the
        predicate defers to the valuation's own fallback
        behavior. Callers that need to assert a fully-loaded status
        should do so at their own boundary.
    """
    if txn.is_deleted:
        return False
    return status_contributes_to_balance(txn)


def is_projected(row: Transaction | Transfer) -> bool:
    """Return True iff *row*'s status is ``Projected``.

    Centralizes the inline ``status_id != ref_cache.status_id(
    StatusEnum.PROJECTED)`` and ``status_id == projected_id`` comparisons
    that recur across ``cash_ledger``, ``grid.py``, and
    ``credit_workflow.py``. The comparison is pure status equality and
    does not consider ``is_deleted`` -- callers that need the combined
    "live and balance-contributing" gate compose this predicate with
    ``is_balance_contributing``, or use ``is_balance_contributing``
    alone when they only need the exclusion set semantics.

    **It takes a ``Transaction`` OR a ``Transfer``**, which is the same
    generality :func:`is_projected_clause` has always had one tier down: both
    tables carry a ``status_id`` into ``ref.statuses``, and "is this row still
    Projected" is one question with one answer. It was annotated
    Transaction-only until plan step X-au-b needed it for a transfer
    (:func:`app.services.cash_ledger.resolve_transfer_amount`), where the only
    alternative was a second spelling of this comparison -- the drift this
    module exists to prevent.

    Args:
        row: a ``Transaction`` or ``Transfer`` with ``status_id`` populated.

    Returns:
        ``True`` if ``row.status_id`` equals the cached integer ID for
        ``StatusEnum.PROJECTED``; ``False`` for every other status,
        including ``Paid``, ``Received``, ``Credit``, ``Cancelled``,
        and ``Settled``.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)


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

    Centralized the inline ``status_id == done_id`` comparison in
    ``entry_service``'s actual-recompute hook. Pure status equality, does
    not consider ``is_deleted``.

    **It has NO caller in ``app/`` as of plan step X-ap**, which merged that
    hook with its posting-reconcile sibling onto the settled BAND -- the two
    halves of one act were grading the same row differently (finding
    **N-229**). Reported for plan step **X-e**, whose subject is exactly the
    callerless public helper, rather than deleted inside a step scoped to the
    settle doors.

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


def is_archived(txn: Transaction) -> bool:
    """Return True iff *txn*'s status is the TERMINAL ``Settled``.

    **Read the name twice: this is not the settled BAND.**
    ``Status.is_settled`` is True for Paid, Received AND Settled -- "the money
    moved" -- and is what every balance and posting reader consumes.  This
    predicate is the single terminal status ``StatusEnum.SETTLED``, the archive:
    the state machine gives it no outgoing edge but identity, so a row that
    reaches it is a historical record.  The two are one word apart and mean
    opposite-sized things, which is exactly why the equality gets a NAME rather
    than being spelled inline beside ``status.is_settled`` in the same function
    -- the shape finding **N-229** is made of.

    Added at plan step X-ap for ``entry_service``, which must refuse a purchase
    recorded against an archived envelope: the row's cost is already history and
    a new entry would either be silently inert or retroactively rewrite what the
    books say it cost.  Pure status equality; does not consider ``is_deleted``.

    Production carries ZERO rows in this status (finding **N-177**, which
    proposes deleting it outright as plan step **X-am**), so the predicate is a
    guard against a state the full-edit Status dropdown can still reach rather
    than a description of live data.

    Args:
        txn: a ``Transaction`` instance with ``status_id`` populated.

    Returns:
        ``True`` if ``txn.status_id`` equals the cached integer ID for
        ``StatusEnum.SETTLED``; ``False`` for every other status, INCLUDING the
        other two members of the settled band.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return txn.status_id == ref_cache.status_id(StatusEnum.SETTLED)


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
