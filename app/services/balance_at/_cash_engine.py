"""
Shekel Budget App -- the last anchor-forward cash producer (retiring at X-c2c).

ONE producer now: :func:`balances_for`, the entries-aware anchor-forward
roll-up (E-25; CRIT-01 / F-009).  Every cash-flow and net-worth surface used to
read it; since plan step X-c2b2 they read the FOLD
(:mod:`app.services.balance_at._cash_fold`), and what survives here is the BASE
the investment growth curve and the property appreciation curve compound over
(:mod:`app.services.balance_at._investment`, its only two callers).  Those two
carry ruled PRE-ANCHOR models of their own -- a reverse growth projection and a
flat anchor carry -- which the fold must not silently replace, so windowing them
onto it is its own step (plan step X-c2c, finding N-43) and this module lives
until then.

**Three names deleted at plan step X-c2b3**, each with the surface that read it:

  * ``balance_as_of_date`` -- the same projection at a calendar DATE rather than
    a period boundary (E-27 / HIGH-02).  The seam scalar
    :func:`app.services.balance_at.cash_balance_at` is the fold read at one
    date, so a date-precise answer no longer needs a second walk; the two stood
    ``$15.96`` apart on the real Checking account (finding cash D2).
  * ``_project_to_period_before`` -- that scalar's prefix roll-forward.
  * ``BalanceResult`` -- the frozen output wrapper.  Its second field was the
    ``stale_anchor_warning``, and the fold makes staleness UNREPRESENTABLE: a
    row settled after the last assertion now moves the balance instead of
    warning that it might not have.  Plan step X-c2b2 deleted the banner that
    rendered the flag, leaving a one-field wrapper, so this producer returns the
    map itself (finding N-50).  The flag is still COMPUTED one level down in
    ``_calculator`` and discarded here; it deletes with that module at X-c2c,
    where its 2-tuple return has no caller left to update.

Split at plan step D1a.  This module held three separable concerns and sat at
exactly 1000 lines, pylint's default module ceiling, because of it.  The other
two moved out to modules named for what they are, and at D1d the producers
moved INTO the seam: this is now ``balance_at._cash_engine``, private to the
package (W9910's since plan step D3).  At D1c the FACTS it folds over landed in
the one :mod:`app.services.cash_ledger` leaf -- the cash counterpart of
:mod:`app.services.loan_ledger` -- alongside the per-row valuation rules that
had been stranded inside ``balance_calculator`` (finding N-30):

  * ``cash_ledger._facts`` -- the FACTS a balance is folded from:
    :func:`~app.services.cash_ledger.resolve_anchor` (the dated anchor SoT,
    E-19 / CRIT-01 / F-001) and
    :func:`~app.services.cash_ledger.load_balance_transactions`.
  * ``cash_ledger._amounts`` -- what ONE row is WORTH:
    :func:`~app.services.cash_ledger.live_amount_overrides`,
    :func:`~app.services.cash_ledger.income_amount`, and the private
    three-bucket reservation formula they build on.
  * ``cash_ledger._flows`` -- what a SET of rows SUMS TO:
    :func:`~app.services.cash_ledger.sum_projected`, the one per-row valuation
    this walk reduces through.  Its per-period ``period_subtotal`` /
    ``period_subtotals`` / ``PeriodSubtotal`` siblings deleted at plan step
    X-c2b3: ruling R-K changed what a subtotal COUNTS, so the seam's
    ``_cash_fold.cash_period_view`` is their successor rather than their peer.

The arrow runs one way: this module imports ``cash_ledger`` (it folds those
facts and reuses those per-row rules); ``cash_ledger`` imports no producer.

Background (entries-aware producer, Commit 5).  CRIT-01 / F-009: the
audit's symptom #1 ($160 on grid vs $114.29 on /savings for the same
inputs) traced to ``cash_ledger._amounts._entry_aware_amount``'s
silent-degrade short-circuit: when the consuming query did NOT
``selectinload(Transaction.entries)``, the helper returned
``txn.effective_amount`` unchanged instead of applying the entry
reduction ``max(estimated - cleared_debit - sum_credit,
uncleared_debit)``.  That made every checking-style balance a
function of an arbitrary ORM eager-loading detail in the caller's
query rather than of the underlying data.  E-25's correction:
exactly one canonical producer owns the transaction query AND
guarantees entries are loaded, so the formula is unconditionally
applied.  Commit 5 also softens the seam inside the entry-aware rule itself
(now ``cash_ledger._amounts``) so that any not-yet-routed caller lazy-loads
entries via the relationship descriptor and gets the correct entries-aware
value rather than silently the wrong one.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).
This module takes plain data, returns plain data, never imports
``flask``/``request``/``session``/``current_app``/``render_template``.

Decimal discipline (``docs/coding-standards.md``).  Every returned balance is
quantized to cents via :func:`~app.utils.money.round_money`
(``ROUND_HALF_UP``), never Python's implicit ``ROUND_HALF_EVEN`` (E-26 /
HIGH-04).
"""

from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.services.cash_ledger import (
    live_amount_overrides,
    load_balance_transactions,
    resolve_anchor,
)
from app.utils.money import round_money

from . import _calculator


def balances_for(
    account: Account,
    scenario_id: int,
    periods: list[PayPeriod],
    *,
    amount_overrides: dict[int, Decimal] | None = None,
) -> OrderedDict[int, Decimal]:
    """Project end balances for ``account`` across ``periods`` (E-25 SoT).

    The entries-aware anchor-forward producer.  Resolves the anchor via
    :func:`resolve_anchor`, owns the transaction query (which always
    eager-loads ``entries``), reuses
    :func:`~app.services.balance_at._calculator.calculate_balances` for
    the pure carry-forward math (CLAUDE.md rule 10: do not rewrite
    the engine), and applies :func:`~app.utils.money.round_money` at
    the boundary so every returned balance is a 2dp ``Decimal`` with
    ``ROUND_HALF_UP`` -- never Python's implicit ``ROUND_HALF_EVEN``
    (E-26 / HIGH-04).

    By construction the result does NOT depend on whether the caller
    pre-loaded entries: the producer issues the query itself with the
    required ``selectinload``.  Pre-Commit-5, grid/dashboard
    pre-loaded entries (correct value) while ``/savings``,
    ``/accounts``, calendar, year-end, investment, and retirement did
    not (silently wrong value).  This is the structural fix for CRIT-01 /
    F-009 / symptom #1.

    Algorithm:

      1. Resolve the anchor (raises for the post-Commit-3 unreachable
         no-history case; never silently degrades).
      2. Query the account's contributing transactions for the
         scenario across the period span, with entries eager-loaded.
      3. Delegate to ``calculate_balances`` for anchor + post-anchor
         period-by-period roll-forward (the engine's
         ``sum_projected`` applies the entry-aware reduction).
      4. Quantize each balance to cents with
         :func:`~app.utils.money.round_money`.

    Args:
        account: The :class:`~app.models.account.Account` to project.
            Must be attached to ``db.session``; Commit 3 guarantees
            it has a resolvable anchor.
        scenario_id: The scenario id; used to filter transactions
            and forwarded into :func:`resolve_anchor` for symmetry
            (anchors are not scenario-scoped at the storage tier
            today; see :func:`resolve_anchor`'s docstring).
        periods: Pay periods to project over, ordered by
            ``period_index``.  Must include the anchor period (the
            engine carries the running balance forward from the
            anchor period only); pre-anchor periods in the list are
            ignored by the engine and absent from the result.
        amount_overrides: Optional ``{transaction_id: Decimal}`` live
            projected-net map (Workstream B).  When None (the default,
            and what every remaining caller passes), it is built here via
            :func:`live_amount_overrides` so the base gets live salary
            income for free.

    Returns:
        ``OrderedDict`` mapping ``pay_period.id`` to the projected end
        balance for that period as a 2dp ``Decimal``.  Pre-anchor periods
        are ABSENT (this producer does not project backwards from the
        anchor -- the totality the fold has, and the reason its two
        remaining consumers carry their own pre-anchor models); the anchor
        period and every period forward are present.  Insertion order
        matches the input ``periods`` list.
    """
    anchor = resolve_anchor(account, scenario_id)
    period_ids = [p.id for p in periods]
    transactions = load_balance_transactions(account, scenario_id, period_ids)

    # Workstream B: projected salary income is recomputed live from the
    # salary profile; the stored estimated_amount is a cache.  Built here
    # when the caller did not supply one, so a consumer gets live income
    # for free.
    if amount_overrides is None:
        amount_overrides = live_amount_overrides(
            account, scenario_id, transactions,
        )

    # The second element is the stale-anchor flag, which no surface has read
    # since plan step X-c2b2 deleted its banner: the fold moves the balance a
    # settled row belongs in rather than warning that the anchor might not
    # cover it.  It is discarded here and deletes with ``_calculator`` at plan
    # step X-c2c (finding N-50).
    raw_balances, _ = _calculator.calculate_balances(
        anchor_balance=anchor.balance,
        anchor_period_id=anchor.period.id,
        periods=periods,
        transactions=transactions,
        amount_overrides=amount_overrides,
    )

    return OrderedDict(
        (period_id, round_money(balance))
        for period_id, balance in raw_balances.items()
    )
