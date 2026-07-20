"""
Shekel Budget App -- Cash ledger: what ONE row is WORTH to checking.

The per-transaction valuation rules, and nothing that sums or folds them.  Given
a single row, these answer "how much of this hits the checking balance right
now?" -- the cash analog of :mod:`app.services.loan_ledger._split`, which
answers the same question for one loan payment (principal / interest / escrow).

Two rule families live here, and they compose in one direction:

  * :func:`live_amount_overrides` PRODUCES the ``{transaction_id: Decimal}``
    map of what rows are worth right now when their stored amount is a stale
    cache; and
  * :func:`income_amount` / :func:`_expense_amount` CONSUME that map (through
    the shared :func:`_override_for` lookup), falling back to the stored figure
    -- and, for an expense carrying entries, to the entries-aware reservation
    formula :func:`_entry_checking_impact`.

**Why they are one module (plan step D1c).**  They were split across two:
``live_amount_overrides`` sat in the cash event sources while the four rules
that read its output sat inside ``balance_calculator``, a PRODUCER module, where
the fence had ruled all four explicit non-producers.  A producer module holding
the valuation rules is what stranded them (finding N-30) -- and
:func:`_entry_checking_impact` is the formula behind the grid-vs-savings
divergence (F-002 Pair C / E-25), which a sibling module documented itself as
"mirroring".  One home is what stops two producers agreeing by coincidence;
D1c deleted that mirror, so the formula now has a single caller and is private.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain data
in, ``Decimal`` out; no Flask import, no writes.
"""

from __future__ import annotations

from decimal import Decimal

from app.utils.balance_predicates import is_projected


def _override_for(txn, amount_overrides):
    """Return ``txn``'s live override amount, or ``None`` when it has none.

    The one override lookup both valuation legs share.  ``income_amount`` and
    :func:`_expense_amount` each carried an identical four-line copy of it --
    harmless while they sat in a producer module, but this module's whole claim
    is that the per-row rules have ONE home, and two copies of the seam's
    entry condition is the shape that claim exists to prevent.

    Args:
        txn: The Transaction being priced.
        amount_overrides: Optional ``{transaction_id: Decimal}`` map, or None.

    Returns:
        The override ``Decimal`` when the map has one for ``txn``, else None.
    """
    if amount_overrides is None:
        return None
    return amount_overrides.get(txn.id)


def _entry_checking_impact(entries, estimated_amount):
    """Three-bucket checking reservation for a sequence of debit/credit entries.

    The core of the entry-aware reduction, with exactly ONE caller:
    :func:`_entry_aware_amount` below, which owns both the as-of window and the
    empty-entries short-circuit.

    **It is private, and D1c is what made that the honest answer.**  It was
    public for one documented reason -- ``balance_resolver`` held a second copy
    of the reduction (``_entry_aware_amount_dated``) and reached in here for the
    shared bucketing so the two paths "could not drift between the two balance
    paths".  D1c deleted that copy: the window is a parameter now, so there is
    no second path to keep in step, and a public name justified by a caller that
    no longer exists is the stale-rationale shape finding N-30 is about.  Being
    private also retires its W9909 ruling -- structure doing what a fence entry
    was doing, which is the whole point of Phase D.

    Partitions the supplied entries into three buckets and returns the portion
    of the budget still held back against checking:

        cleared_debit   = sum(amount where not is_credit and     is_cleared)
        uncleared_debit = sum(amount where not is_credit and not is_cleared)
        sum_credit      = sum(amount where is_credit)

        impact = max(estimated_amount - cleared_debit - sum_credit,
                     uncleared_debit)

    Cleared debits are already reflected in the checking anchor balance,
    so they are subtracted from the reservation.  Uncleared debits act
    as a floor -- the reservation can never be smaller than uncleared
    checking hits, which also handles overspend.  A credit entry never
    hits checking directly (it flows through a CC Payback sibling
    transaction), so it only reduces the reservation.

    This function sees whatever entry set it is handed and applies the
    bucketing to all of it.  Windowing by ``as_of`` and short-circuiting an
    empty set both belong to the caller, and there is now exactly one, so those
    two decisions are made once rather than kept in step across two paths.

    Args:
        entries: An iterable of entry rows, each exposing ``amount``
            (Decimal), ``is_credit`` (bool), and ``is_cleared`` (bool).
            The caller is responsible for any date/window filtering and
            for short-circuiting an empty sequence before calling.
        estimated_amount: Decimal -- the transaction's budgeted amount,
            the reservation ceiling before debits and credits reduce it.

    Returns:
        Decimal -- the amount this transaction's entries hold back from
        the checking balance.
    """
    cleared_debit = Decimal("0")
    uncleared_debit = Decimal("0")
    sum_credit = Decimal("0")
    for entry in entries:
        if entry.is_credit:
            sum_credit += entry.amount
        elif entry.is_cleared:
            cleared_debit += entry.amount
        else:
            uncleared_debit += entry.amount

    return max(
        estimated_amount - cleared_debit - sum_credit,
        uncleared_debit,
    )


def _entry_aware_amount(txn, as_of=None):
    """Compute the checking-balance impact for a single expense transaction.

    For projected expenses with entries (loaded eagerly or
    lazy-loaded on demand), the formula partitions debit entries into
    cleared and uncleared buckets, then holds back only the portion
    of the budget that has not yet been reconciled with the anchor:

        cleared_debit   = sum(entries where not is_credit and     is_cleared)
        uncleared_debit = sum(entries where not is_credit and not is_cleared)
        sum_credit      = sum(entries where is_credit)

        checking_impact = max(
            estimated_amount - cleared_debit - sum_credit,
            uncleared_debit,
        )

    Semantics:
      - A cleared debit is already reflected in the checking anchor
        balance, so it should not come out of the projection again --
        we subtract it from the reservation.
      - An uncleared debit has hit real checking but is NOT yet in the
        anchor, so the full estimated amount must still be held back
        (the max() floor handles this and also handles overspend where
        uncleared debits exceed the remaining reservation).
      - A credit entry never hits checking directly -- it flows through
        a CC Payback sibling transaction -- so it only reduces the
        reservation.
      - With every is_cleared = FALSE (the default for new entries),
        cleared_debit = 0 and the formula reduces to
        max(estimated - sum_credit, uncleared_debit), which matches
        the pre-cleared-flag behavior from scope doc section 4.2.

    Example (the user's grocery bug):
      est = 500, three cleared debit purchases summing to 462.34.
      checking_impact = max(500 - 462.34 - 0, 0) = 37.66, which is the
      remaining budget to hold back now that the anchor reflects the
      first three purchases.

    Seam removed (Commit 5 / CRIT-01 / F-009 / E-25): the pre-Commit-5
    implementation guarded the entry formula behind an
    eager-load presence check on the relationship (the ``entries``
    key in the SQLAlchemy instance dict), and returned
    ``txn.effective_amount`` whenever that check missed.  That
    silently degraded to the non-entries-aware value whenever the
    consuming query had not issued
    ``selectinload(Transaction.entries)``.  Symptom #1 ($160 on grid
    vs $114.29 on /savings for the same data) is exactly that seam in
    production: the grid eager-loaded entries and computed the
    reduction; /savings did not and got back ``estimated_amount``
    unchanged.  E-25's correction makes the canonical producer
    ``app.services.balance_resolver.balances_for`` always
    eager-load entries (through
    :func:`app.services.cash_ledger._facts.load_balance_transactions`),
    so this function never sees an unloaded relationship from a routed
    caller.  The remaining ``getattr(txn, "entries", ())`` access below
    covers two safe cases:

      * **Not-yet-routed ORM callers** (savings/accounts/calendar/
        year-end/investment/retirement, fixed in Commits 6-9): the
        SQLAlchemy descriptor lazy-loads the relationship.  The
        caller now gets the CORRECT entries-aware value with one
        extra SELECT per transaction (acceptable for the transition;
        the producer routing eliminates the extra query).
      * **Non-ORM test fakes** with no ``entries`` attribute:
        ``getattr`` returns the default ``()``, the empty-entries
        early return fires, and the function returns
        ``effective_amount`` -- the same behavior pre-Commit-5 had
        for test fakes.

    What is no longer possible: the same Projected envelope expense
    yielding two different values for two different consumers based
    purely on whether their query happened to ``selectinload``.

    **The as-of window is a PARAMETER, not a second function (plan step
    D1c).**  ``balance_resolver`` carried a near-identical
    ``_entry_aware_amount_dated`` whose docstring said "the formula is
    otherwise identical to the engine helper" -- two copies of the
    checking-reservation rule, kept in step by hand, which is the
    agreeing-by-coincidence shape this arc exists to kill (and which
    ``duplicate-code`` reported the moment both copies called the same
    ``income_amount``).  One rule with an optional bound cannot drift, and
    it makes the choice VISIBLE at the call site: a caller now passes a
    date or does not, rather than having to remember which of two helpers
    the calendar surfaces need.

    Args:
        txn: A Transaction object.  The ``entries`` relationship may
            be eager-loaded (canonical producer), unloaded
            (transitional caller; lazy-loads on demand), or absent
            (test fake).
        as_of: Optional calendar date bounding entry inclusion (E-27 /
            HIGH-02 / W-277).  ``None`` (the default) counts every loaded
            entry.  With a date, entries dated AFTER it are excluded: a
            purchase that has not happened yet cannot have cleared the
            bank as of that date, and counting it would reduce the
            reservation prematurely and ship a wrong balance for a
            calendar month-end.

    Returns:
        Decimal -- the amount this transaction contributes to checking
        balance.
    """
    # ``getattr`` with a default of ``()`` handles both unloaded ORM
    # relationships (descriptor lazy-loads via the session) and
    # non-ORM fakes (no attribute defined).  The empty-tuple default
    # passes the falsy check below, mirroring the original empty-list
    # short-circuit and keeping non-ORM tests stable.
    entries = getattr(txn, "entries", ())
    # This check stays AHEAD of ``is_projected`` and that ordering is
    # load-bearing, not stylistic: ``is_projected`` reads ``txn.status_id``
    # through ``ref_cache`` and so raises on a non-ORM fake, which the
    # no-entries short-circuit above is documented to keep working.
    if not entries:
        return txn.effective_amount

    # Only apply the entry formula to projected transactions.
    # Settled, cancelled, and credit statuses are already handled
    # correctly by effective_amount (returns 0 for excluded statuses,
    # actual_amount for settled statuses).  Routed through the
    # centralized ``is_projected`` predicate (D6-09 / MED-02) so
    # this entry-formula gate cannot drift from the other
    # Projected-only filters in this package and in the balance
    # resolver.
    if not is_projected(txn):
        return txn.effective_amount

    if as_of is not None:
        entries = [entry for entry in entries if entry.entry_date <= as_of]
        if not entries:
            # No purchase has occurred yet as of ``as_of``; the full
            # estimated reservation is still pending.  ``effective_amount``
            # collapses to estimated for an unfilled Projected expense
            # (actual_amount is unset until it settles), so this matches
            # the unwindowed empty-entries branch above.
            return txn.effective_amount

    # Partition the entries and hold back the unreconciled budget.  The
    # bucketing rule and the reservation formula live once, in
    # ``_entry_checking_impact`` (E-27).
    return _entry_checking_impact(entries, txn.estimated_amount)


def income_amount(txn, amount_overrides):
    """Return the income contribution for ``txn``, honoring a live override.

    Part of this module's public surface (no leading underscore): the
    canonical cash producer ``balance_resolver``'s date-cut income leg
    reuses it so the override seam resolves identically on both paths.
    (The expense analogue stays private -- the resolver's date-cut expense
    leg has its own variant rather than calling ``_expense_amount``.)

    ``amount_overrides`` is the live projected-net seam (Workstream B):
    a dict mapping transaction id -> Decimal produced by
    :func:`live_amount_overrides` below.  When the
    transaction's id is present, the live-recomputed net is used in
    place of the stored ``effective_amount`` so a projected salary
    paycheck reflects the current salary profile rather than a cached
    amount a later profile/calibration/code change may have invalidated.
    ``amount_overrides=None`` (the default everywhere this module is
    called without the seam) returns ``effective_amount`` unchanged, so
    the pre-seam behavior is byte-identical.

    Args:
        txn: An income Transaction.
        amount_overrides: Optional ``{transaction_id: Decimal}`` map, or
            None.

    Returns:
        Decimal -- the override amount when present, else
        ``txn.effective_amount``.
    """
    override = _override_for(txn, amount_overrides)
    return txn.effective_amount if override is None else override


def _expense_amount(txn, amount_overrides, as_of=None):
    """Return the expense contribution for ``txn``, honoring a live override.

    The expense-leg analogue of :func:`income_amount`.  When the
    transaction's id is in ``amount_overrides`` (the live-derive seam --
    e.g. a recurring loan-payment transfer whose cash debit is derived
    from the destination loan via
    :func:`app.services.loan_payment_service.live_loan_transfer_amounts`),
    the live amount replaces the stored figure.  Otherwise it falls back
    to :func:`_entry_aware_amount`, preserving the entry-checking formula
    for envelope expenses.  ``amount_overrides=None`` (or a txn id absent
    from the map) returns the entry-aware amount unchanged, so non-loan
    expenses and the pre-seam behavior are byte-identical.

    An override WINS over the as-of window, exactly as it wins over the
    entry formula: a live-derived amount is what the row is worth now, and
    it carries no entries to window.

    Args:
        txn: An expense Transaction.
        amount_overrides: Optional ``{transaction_id: Decimal}`` map, or
            None.
        as_of: Optional calendar date bounding entry inclusion, forwarded
            verbatim to :func:`_entry_aware_amount`; ``None`` counts every
            loaded entry.

    Returns:
        Decimal -- the override amount when present, else
        :func:`_entry_aware_amount`.
    """
    override = _override_for(txn, amount_overrides)
    return _entry_aware_amount(txn, as_of) if override is None else override


def live_amount_overrides(account, scenario_id, transactions):
    """Build the live per-transaction amount-override map for ``transactions``.

    Merges two read-time live-recompute seams, both keyed by transaction
    id, both treating the stored amount as a cache a later profile,
    calibration, escrow/rate, or financial-calc CODE change may have
    invalidated without firing a regeneration:

    * :func:`app.services.income_service.live_projected_net` -- projected
      salary income reflects the current salary profile.
    * :func:`app.services.loan_payment_service.live_loan_transfer_amounts`
      -- a recurring loan-payment transfer's cash debit reflects the
      loan's current monthly payment (P&I + escrow).

    The two key sets are disjoint (salary income transactions vs
    loan-payment transfer shadows), so the merge cannot collide.  Both
    helpers are imported locally to keep their (paycheck/tax and
    loan-resolver) stacks off this module's load path and out of any
    import cycle.  Returns an empty dict when neither seam has a
    candidate -- the common case -- so the override threading stays a
    structural no-op for those surfaces.

    The map this returns is what :func:`income_amount` and
    :func:`_expense_amount` beside it consume; producing it and reading it
    are one concern, which is why they share a module (plan step D1c).

    Args:
        account: The :class:`~app.models.account.Account` whose rows are
            being priced; only its ``user_id`` is read (the income seam
            scopes its salary lookup by user).
        scenario_id: The scenario the amounts are resolved under.
        transactions: The loaded rows to price.  Each seam picks its own
            candidates out of this list and ignores the rest.

    Returns:
        ``dict`` mapping ``transaction_id`` to the live ``Decimal``
        amount, empty when neither seam has a candidate.
    """
    # Pylint: ``import-outside-toplevel`` -- imported locally to keep the
    # income_service (paycheck/tax) and loan_payment_service (loan-resolver)
    # stacks off this module's load path and out of any import cycle; the
    # helpers are only needed at call time.
    # pylint: disable=import-outside-toplevel
    from app.services import income_service, loan_payment_service
    income_overrides = income_service.live_projected_net(
        account.user_id, scenario_id, transactions,
    )
    loan_overrides = loan_payment_service.live_loan_transfer_amounts(
        scenario_id, transactions,
    )
    return {**income_overrides, **loan_overrides}
