"""
Shekel Budget App -- What ONE row is worth, without asking any producer.

The pure half of the cash valuation: the arms that answer from the row itself
-- a status that excludes it, a soft delete, a figure a human typed, and the
figure the row OWNS.  Nothing here consults the amount model's live producers,
which is the whole reason it is a module of its own.

**It is a LEAF because the loan stack may not NAME the cash-ledger package,
and the gate that forbids it is pylint's** (plan step X-au-c2).
:mod:`~app.services.cash_ledger._amount_source` reaches UP into
``loan_payment_service`` for amount rule 4's producer
(``LoanPricing``, ``loan_payment_config``).  Both of those
imports are DEFERRED to call time, so the RUNTIME module graph is acyclic and
importing :mod:`app.services.cash_ledger` pulls in no loan service at all --
that much an adversarial review measured, correcting an earlier draft of this
paragraph which claimed a runtime cycle.  What is NOT acyclic is the graph
pylint builds: ``cyclic-import`` (R0401) traces function-level imports too, so
a module-level ``from app.services.cash_ledger import ...`` anywhere reachable
from ``loan_payment_service`` -- ``loan_ledger``, ``loan_loaders``,
``loan_ledger._split`` -- produces the finding.  Measured: adding those imports
raised EIGHT R0401s where ``pylint app/`` had none, and deferring them on the
loan side left all eight standing, because deferral is exactly what pylint
declines to honour.  ``CLAUDE.md``'s Definition of Done requires that gate
clean, so the readers in that stack take the arms below -- none of which touch
a producer -- from a module below both tiers.  There is still exactly ONE
definition of each rule, which is the claim
:mod:`app.services.cash_ledger._amounts` exists to make; it simply lives in a
file both tiers can reach.

**What is genuinely inverted is the reach this file routes around**: the amount
model is the LOWER tier and should not be asking a loan service to price
anything.  Unwinding that belongs to plan step **X-au-g**, which rebuilds rule
4's producer; recording it here rather than hiding it is the point.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01): ORM rows in,
``Decimal`` out; no Flask import, no writes, no queries.
"""

from decimal import Decimal

from app.exceptions import AmountUnresolvable
from app.utils.balance_predicates import is_balance_contributing


def fixed_contribution(txn) -> "Decimal | None":
    """Return what *txn* is worth WITHOUT resolving its amount, or ``None``.

    The one statement of the two arms that answer before the amount model is
    consulted at all, so every valuation built on it -- the batch, the one-row
    form, and the cheap accessor -- cannot come to disagree about them:

      * a row that does not contribute -- soft-deleted, Credit or Cancelled --
        is worth ``0``, whatever prices it; and
      * a row carrying a human's ``actual_amount`` is worth that, because a
        figure somebody read off a statement is a fact and a derivation is an
        inference.

    ``None`` means neither applies and the row's own amount decides, which is
    the resolver's question.  It reads as "the actual, when there is one" by
    construction rather than by a second test: ``actual_amount`` is ``None``
    exactly when there is none.

    **The first arm is why the status gate sits ABOVE the resolver** (plan step
    X-au-c2).  ``Transaction.effective_amount`` answered ``$0.00`` for an
    excluded row from inside the valuation, where the resolver would REFUSE the
    same row: both live producers filter to Projected rows
    (``income_service.live_projected_net``,
    ``loan_payment_service.LoanPricing.live_cash``), so a Cancelled salary
    row is absent from their maps and has no derived answer at all.  Asking what
    a row is worth before asking what it is priced at is what keeps that from
    being a 500 on a row nobody is counting.

    Args:
        txn: The row being valued.  ``is_deleted`` and the ``status``
            relationship are read (``status`` is ``lazy="joined"``), then
            ``actual_amount``.

    Returns:
        The row's worth when it needs no resolution, else ``None``.
    """
    if not is_balance_contributing(txn):
        return Decimal("0")
    return txn.actual_amount


def own_figure(amount, kind: str, row_id: int) -> Decimal:
    """Return a row's OWN stored figure, refusing a row that carries none.

    The OWN rule's whole body, and the refusal in it is the amount model's
    TOTALITY contract rather than defensive padding: a resolver that can answer
    ``None`` for a row is not total, and every other rule beside it raises
    rather than returning one.  It is unreachable on today's DATA -- no row's
    amount column is NULL yet -- and what keeps it that way is
    ``ck_transactions_amount_ownership`` (plan step X-au-c1): a row that owns
    its amount must store one.  A row that reaches here with no figure has that
    CHECK broken, and substituting a zero would remove real money from a
    balance in silence.

    Args:
        amount: The row's stored amount column.
        kind: ``"transaction"`` or ``"transfer"``, for the refusal message.
        row_id: The row's id, named in the refusal.

    Returns:
        The stored figure.

    Raises:
        AmountUnresolvable: When the row owns its amount and stores none.
    """
    if amount is None:
        raise AmountUnresolvable(
            f"{kind.capitalize()} {row_id} owns its amount and carries none. "
            "A row whose amount is its OWN must store it -- that pairing is "
            "ck_transactions_amount_ownership -- so this row was written "
            "around the CHECK. There is deliberately no substitute figure: "
            "answering zero would take real money out of a balance without "
            "saying so."
        )
    return amount


def owned_amount(txn) -> Decimal:
    """Return the amount a row that OWNS its figure states, refusing otherwise.

    The BUDGET half of the pair :func:`owned_contribution` completes (plan step
    X-au-c2b): the cheap accessor for a reader that takes a row's own AMOUNT and
    can only ever see rows whose amount is their own -- which after the freeze
    (plan step X-au-c3) means every SETTLED row.  Those readers filter to
    settled statuses in SQL, so building a basis for them would run the paycheck
    engine to re-derive a figure the row already holds.

    **It answers the ESTIMATE, never an entered actual**, which is the whole
    distinction from :func:`owned_contribution` beside it.  Ruling E-21: a row's
    budget base is ``estimated_amount`` unconditionally, so a variance's two
    terms -- what was planned, and what was spent -- stay two different reads.
    Answering the actual here would make every settled row's variance zero by
    construction, which is the defect the spending report's surprises list
    exists to surface.

    **The name is the assertion, and the refusal is what makes it one**: a row
    whose amount is DERIVED carries none, so this raises rather than handing a
    ``None`` into a subtraction.  See :func:`owned_contribution` for why that
    refusal is what makes the per-kind cutovers safe to ship one at a time.

    Args:
        txn: The row whose ``estimated_amount`` is its own.

    Returns:
        The row's stated budget.

    Raises:
        AmountUnresolvable: When the row's amount is derived, so it stores none.
    """
    return own_figure(txn.estimated_amount, "transaction", txn.id)


def owned_contribution(txn) -> Decimal:
    """Return what a row that OWNS its figure contributes.

    The cheap accessor for a reader that can only ever see rows whose amount is
    their own -- which after the freeze (plan step X-au-c3) means every SETTLED
    row, and which is what the loan split, the loan posting sync and reconcile,
    the settled-spend metric and the spending report all read.  Those readers
    filter to settled statuses in SQL, so building a basis for them would run
    the paycheck engine to re-derive a figure the row already holds.

    **The name is the assertion, and the refusal is what makes it one.**  A row
    whose amount is DERIVED carries none, and
    ``ck_transactions_amount_ownership`` is what pairs the two -- so this raises
    where ``effective_amount`` used to, rather than answering ``None`` into a
    money path.  A reader that a later cutover routes derived rows into fails
    LOUDLY here at that moment instead of publishing a wrong number, which is
    what makes the per-kind cutovers (X-au-d..X-au-i) safe to ship one at a time.

    **One reader takes it for a different reason, and that reason is a CYCLE**:
    ``loan_payment_service.get_payment_history`` is NOT settled-only -- its
    query admits Projected shadows -- but the rule that would price one routes
    back through it (rule 4 -> ``LoanPricing.derive_cash`` ->
    ``_resolve_loan_basis`` -> ``load_loan_context`` -> that function).  So the
    loan-side INCOME leg must keep owning its figure and only the checking-side
    EXPENSE leg can be declared derived, which is a bound plan step X-au-g
    inherits and which this refusal is what enforces.

    Args:
        txn: The row being valued, whose ``estimated_amount`` is its own.

    Returns:
        ``0`` for a row that contributes nothing, the entered ``actual_amount``
        when there is one, else the row's stored ``estimated_amount``.

    Raises:
        AmountUnresolvable: When the row's amount is derived, so it stores none.
    """
    fixed = fixed_contribution(txn)
    if fixed is not None:
        return fixed
    return owned_amount(txn)
