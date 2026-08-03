"""
Shekel Budget App -- Cash ledger: what ONE row is WORTH to checking.

The per-transaction valuation rules, and nothing that sums or folds them.  Given
a single row, these answer "how much of this hits the checking balance right
now?" -- the cash analog of :mod:`app.services.loan_ledger._split`, which
answers the same question for one loan payment (principal / interest / escrow).

Three rule families live here, split by the question they answer.

What a row is worth while it is still PROJECTED -- a reservation, money not yet
gone -- is two of them, composing in one direction:

  * :func:`live_amount_overrides` PRODUCES the ``{transaction_id: Decimal}``
    map of what rows are worth right now when their stored amount is a stale
    cache; and
  * :func:`income_amount` / :func:`_expense_amount` CONSUME that map (through
    the shared :func:`_override_for` lookup), falling back to the stored figure
    -- and, for an expense carrying entries, to the entries-aware reservation
    formula :func:`_entry_checking_impact`.

What a row is worth once it has SETTLED -- money that really moved -- is the
third, and it is deliberately none of the above:

  * :func:`settled_cash_leg` is ``effective_amount - Sigma(credit entries)``,
    signed by transaction type.  Neither read-time adjustment above can reach a
    settled row (both filter to ``is_projected``), and a reservation would be
    meaningless for cash already gone.  It arrived here at plan step X-a from
    ``posting_service``, so the ledger WRITER and the cash WALK price one row
    through the same function.

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

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.transaction import Transaction
from app.utils.balance_predicates import is_balance_contributing, is_projected


@dataclass(frozen=True)
class ReconciledThrough:
    """The day through which an account's movements are inside a declared balance.

    **The ONE statement of the question this whole arc turns on** -- *is this
    movement already reflected in the balance the user declared?* -- and ruling
    R-DH (a)'s answer to it: an assertion is the CLOSING balance for its civil
    day, so a movement dated at or before that day is inside it by definition.
    :meth:`covers` is that rule, and it is the only implementation of it in the
    codebase -- including the MODELLED side, where an adversarial review found
    a seventh statement of it hiding behind a loose date
    (``balance_at._asset_contributions``: a payroll contribution on a payday
    the assertion already covers is money the asserted balance contains, and
    modelling it again double counts).

    **What is NOT fenced, stated because a fence whose limits are unstated
    reads as stronger than it is.**  :attr:`~._events.CashAnchorFact.observed_on`
    and :attr:`~._events.CashSourceFact.settled_on` are still plain ``date``
    fields -- they have to be, to key a day, sort a stream and date a journal
    entry -- so ``x <= fact.observed_on`` compiles today in any new module.
    That is the shape a lint checker over the assertion-day vocabulary WOULD
    catch, and this type would not: the two fences are complementary rather
    than substitutes (``anchor_settle_partition.md`` Section 14.5).

    **It is a TYPE rather than a bare date because the mistake it prevents is
    the one that cost production ``$4,001.42``.**  The question had FOUR
    implementations when
    ``docs/audits/balance_architecture/anchor_settle_partition.md`` was
    written, three of them comparing different things in different units, and
    the plan's answer was a pylint checker that would flag a fifth.  A checker
    cannot see through ``earliest <= latest``, which is the very site finding
    N-133 / F4 was about, so it would have fenced everything except the one
    with the history.  This class fences it structurally instead: with no
    ordering methods, ``settled_on <= reconciled_through`` raises
    ``TypeError`` rather than silently answering the question a fifth way.
    Asking it correctly and asking it wrongly stopped being the same keystroke.

    A caller that genuinely needs the raw civil day -- an SQL bound, a rendered
    caption -- reads :attr:`observed_day` and says so at the call site.  That
    is the deliberate escape hatch, and naming it is the point: reaching for it
    is visible in review, where a ``<=`` was not.

    **Why the day and not the instant, measured.**  Neither instant available
    is a fact about money: ``Transaction.paid_at`` is ``db.func.now()`` at the
    click and an assertion's ``created_at`` is when it was typed.  So the
    instant partition asked "which button was pressed first" and answered a
    question about cash with it.  On production 2026-07-31 an ordinary session
    -- read the bank, enter ``$1,307.66``, tick off what cleared -- recorded
    three already-cleared payments in the NINE SECONDS after the assertion and
    subtracted ``$4,001.42`` a second time, rendering ``-$4,021.37`` against a
    true ``-$19.95``.  Across four months of that account, 65 of 139 settled
    rows (``$19,602.13`` gross) were classified by click order, and the
    correction the model was forced to plug at each assertion totalled
    ``$40,554.34`` gross / ``-$6,998.90`` net against ``$15,367.94`` /
    ``-$940.06`` under this rule.  The instant partition's own stated evidence
    was ONE 2026-07-25 pair (an anchor at 12:57 UTC, two expenses at 13:07,
    ``$108.15`` a date-keyed rule absorbs -- finding cash D1); scored over the
    whole account rather than that pair, the day rule books a SMALLER plug at
    that very assertion (``$39.27`` against ``$68.88``), because the rows it
    absorbs were overwhelmingly recorded late rather than cleared late.

    **The residual is stated rather than hidden.**  A payment that genuinely
    clears AFTER the balance was observed on the same day is absorbed anyway,
    and the projection reads high until the next assertion.  It is bounded
    (median ``$184.55`` a day against the ``$4,161.47`` the instant rule
    produced on 2026-07-31) and it self-corrects at the next assertion.

    **What removes it is an OBSERVATION, not a second derived date.**  With
    both real dates recorded, a movement made after the balance was read still
    carries the same civil day as one made before it, so no rule comparing two
    dates can tell them apart (``anchor_settle_partition.md`` Section 10.3).
    The guess ends only where the user says what their statement showed --
    which plan step S1-c built for PURCHASES
    (``entry_service.record_settled_days``) and which a bank import would do
    for settles.  Until then this is the best available guess on the settle
    side, and saying so is the honest form of it.

    Attributes:
        observed_day: The civil day the account's latest balance assertion is
            the closing balance for (``AccountAnchorHistory.observed_on``), or
            ``None`` when no balance has ever been declared for it.  Read it
            only to render or to bound a query; to ask whether a movement is
            inside the balance, call :meth:`covers`.
    """

    observed_day: date | None

    def covers(self, event_day: date | None) -> bool:
        """Return whether an event dated *event_day* is inside this balance.

        Ruling R-DH (a): an assertion is the closing balance for its civil day,
        so a movement dated at or before that day is inside it.

        **Total in both the argument and the boundary**, so it is a rule rather
        than one with a precondition each caller must remember.  A ``None``
        *event_day* is a purchase whose posting day has never been observed --
        still outstanding, whatever any balance says (ruling R-DH (d) as
        restated at plan step S1-c: the engine never guesses a posting day).  A
        ``None`` :attr:`observed_day` is an account that has never had a
        balance declared, so there is nothing for anything to be inside of.

        **Totality means something different to the two caller shapes, and the
        absorb loops rely on an invariant rather than on this arm.**  For the
        entry reservation a ``None`` day means "this purchase is outstanding",
        which is the answer wanted.  Inside the two walks' ``while`` loops a
        False HALTS absorption for that assertion and every later one, so a
        ``None`` day there would silently short the ledger where the bare
        ``<=`` it replaced would have raised.  It cannot arise:
        :attr:`~app.services.cash_ledger.CashSourceFact.settled_on` is typed
        non-optional and is the STORED ``transactions.settled_on``, read
        through :func:`app.utils.balance_predicates.settled_day`, which REFUSES
        a missing day rather than returning one; the posting walk's source
        loaders resolve their days through the same accessor.  Stated because a
        fail-open substitution in a money path is not something a reader should
        have to re-derive.

        Args:
            event_day: The civil day the money moved -- a settled row's or a
                purchase's ``settled_on`` -- or ``None`` when it has not been
                observed.

        Returns:
            True when the event is already inside the declared balance.
        """
        if event_day is None or self.observed_day is None:
            return False
        return event_day <= self.observed_day


@dataclass(frozen=True)
class ProjectedBasis:
    """What a reader knows about ONE account that changes its plan's WORTH.

    The two per-account facts every still-Projected valuation in this module
    needs, bundled so they travel together and are built ONCE per account
    rather than passed as two arguments a caller could mismatch (plan
    Section 8: an argument a caller can get wrong is a defect, not a
    contract).  It is REQUIRED rather than defaulted, deliberately: a
    forgotten basis would silently make every purchase read as outstanding and
    every envelope hold its whole budget back -- a wrong balance from an
    omission, which is exactly the ``selectinload`` seam CRIT-01 / F-009 closed
    one field over.

    Neither field is a clock.  ``reconciled_through`` is a fact about the
    ACCOUNT (the latest day its owner asserted a balance for it), so what a row
    is worth stays a function of the row and its account -- ruling R-M's
    "the reader's clock decides WHEN a row lands, never what it is worth",
    which is why plan step X-c2c1 could delete the reservation's ``as_of``
    window and why nothing here brings one back.

    Attributes:
        amount_overrides: The live ``{transaction_id: Decimal}`` map
            (:func:`live_amount_overrides`) -- recomputed salary income and
            derived loan debits.  ``{}`` when the account has no candidate.
        reconciled_through: The account's :class:`ReconciledThrough` boundary
            -- the day its owner last declared a balance for it.  A purchase
            whose recorded posting day it ``covers`` is already inside that
            declared balance; every other purchase is still outstanding and its
            envelope keeps holding the whole budget back.
    """

    amount_overrides: dict[int, Decimal]
    reconciled_through: ReconciledThrough


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


def _entry_checking_impact(
    entries, estimated_amount: Decimal,
    reconciled_through: ReconciledThrough,
) -> Decimal:
    """Three-bucket checking reservation for a sequence of debit/credit entries.

    The core of the entry-aware reduction, with exactly ONE caller:
    :func:`_entry_aware_amount` below, which owns the empty-entries
    short-circuit.

    **It is private, and D1c is what made that the honest answer.**  It was
    public for one documented reason -- ``balance_resolver`` held a second copy
    of the reduction (``_entry_aware_amount_dated``) and reached in here for the
    shared bucketing so the two paths "could not drift between the two balance
    paths".  D1c deleted that copy, so there is no second path to keep in step,
    and a public name justified by a caller that no longer exists is the
    stale-rationale shape finding N-30 is about.  Being private also retires its
    W9909 ruling -- structure doing what a fence entry was doing, which is the
    whole point of Phase D.

    Partitions the supplied entries into three buckets and returns the portion
    of the budget still held back against checking:

        settled_debit     = sum(debit amounts already inside the anchor)
        outstanding_debit = sum(debit amounts the bank has not been seen to take)
        sum_credit        = sum(amount where is_credit)

        impact = max(estimated_amount - settled_debit - sum_credit,
                     outstanding_debit)

    A SETTLED debit is already reflected in the checking anchor balance, so it
    is subtracted from the reservation.  An OUTSTANDING debit acts as a floor --
    the reservation can never be smaller than the checking hits the anchor does
    not know about, which also handles overspend.  A credit entry never hits
    checking directly (it flows through a CC Payback sibling transaction), so it
    only reduces the reservation and its own dates are irrelevant.

    **Which bucket a debit falls in is DERIVED, and that is ruling R-DH (d)**
    (plan step S1-c).  It was a stored ``is_cleared`` boolean, written by a bulk
    UPDATE at every anchor true-up over "every entry dated on or before the
    SERVER's today" -- so a purchase recorded BEFORE the true-up was reconciled
    and the identical purchase recorded after it never was, and the difference
    was which button the user pressed first.  Now the purchase carries the day
    the bank was SEEN to have taken it (``settled_on``) and the answer is the
    account's own :class:`ReconciledThrough` boundary: the same rule, in the
    same units, that the read replay and the posting walk apply to a settled
    transaction.

    A purchase whose ``settled_on`` is NULL has never been observed on a
    statement and is OUTSTANDING, which is the conservative arm: the envelope
    keeps holding its whole budget back until the user confirms the money has
    actually left.  Nothing here guesses a posting day on the user's behalf.

    This function sees whatever entry set it is handed and applies the
    bucketing to all of it.  Short-circuiting an empty set belongs to the
    caller, and there is exactly one, so that decision is made once rather than
    kept in step across two paths.

    Args:
        entries: An iterable of entry rows, each exposing ``amount``
            (Decimal), ``is_credit`` (bool), and ``settled_on``
            (``date | None``).  The caller is responsible for short-circuiting
            an empty sequence before calling.
        estimated_amount: Decimal -- the transaction's budgeted amount,
            the reservation ceiling before debits and credits reduce it.
        reconciled_through: The account's :class:`ReconciledThrough` boundary.
            A debit whose ``settled_on`` it ``covers`` is inside that balance.

    Returns:
        Decimal -- the amount this transaction's entries hold back from
        the checking balance.
    """
    settled_debit = Decimal("0")
    outstanding_debit = Decimal("0")
    sum_credit = Decimal("0")
    for entry in entries:
        if entry.is_credit:
            sum_credit += entry.amount
        elif reconciled_through.covers(entry.settled_on):
            settled_debit += entry.amount
        else:
            outstanding_debit += entry.amount

    return max(
        estimated_amount - settled_debit - sum_credit,
        outstanding_debit,
    )


def _entry_aware_amount(txn, reconciled_through: ReconciledThrough) -> Decimal:
    """Compute the checking-balance impact for a single expense transaction.

    For projected expenses with entries (loaded eagerly or
    lazy-loaded on demand), the formula partitions debit entries into
    settled and outstanding buckets, then holds back only the portion
    of the budget the anchor does not already account for:

        settled_debit     = debits whose recorded posting day is inside the
                            account's latest asserted balance
        outstanding_debit = every other debit
        sum_credit        = sum(entries where is_credit)

        checking_impact = max(
            estimated_amount - settled_debit - sum_credit,
            outstanding_debit,
        )

    Semantics:
      - A SETTLED debit is already reflected in the checking anchor
        balance, so it should not come out of the projection again --
        we subtract it from the reservation.
      - An OUTSTANDING debit may or may not have left the account, and
        either way the anchor does not know about it, so the full
        estimated amount must still be held back (the max() floor
        handles this and also handles overspend where outstanding
        debits exceed the remaining reservation).
      - A credit entry never hits checking directly -- it flows through
        a CC Payback sibling transaction -- so it only reduces the
        reservation, whatever its dates say.
      - With every ``settled_on`` NULL (the state a fresh purchase is in,
        and the state migration ``d7c1f4a9e603`` left every existing row
        in), settled_debit = 0 and the formula reduces to
        max(estimated - sum_credit, outstanding_debit) -- the whole budget
        held back, which is the conservative arm and matches the
        pre-cleared-flag behavior from scope doc section 4.2.

    Example (the user's grocery bug):
      est = 500, three debit purchases summing to 462.34, all confirmed
      against a statement whose balance the user then entered.
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
    unchanged.  E-25's correction put the eager load inside the
    LOADER rather than leaving it to each consumer, and plan step X-g4b
    left that property where the fold reads: every row this rule is
    handed comes from
    :func:`app.services.cash_ledger._facts._unwindowed_contributing_rows`,
    which issues ``selectinload(Transaction.entries)`` for both halves
    of the event stream, so this function never sees an unloaded
    relationship from a routed caller.  The remaining
    ``getattr(txn, "entries", ())`` access below covers two safe cases:

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

    **There is no as-of window, and the reason is what an ENTRY is (plan step
    X-c2c1, ruling R-M).**  This carried an optional date bound (E-27 / HIGH-02
    / W-277) that dropped entries dated after the reader's now, so a purchase
    that had not happened could not clear the reservation early.  Ruling R-M
    answered that at the SOURCE instead: an entry RECORDS a purchase that
    happened, so plan step X-c0 refuses a future purchase date at both write
    doors (:func:`app.services.entry_service._reject_future_purchase_date`)
    -- and a purchase that happened belongs in the reservation whatever date the
    reader is asking from.  What a row is WORTH is a function of the row, as
    :func:`settled_cash_leg` beside it already is; the reader's clock decides
    WHEN the row lands (ruling R-G's clamp, in the seam's fold), never what it
    is worth.

    Two measured facts, so the deletion is not read as merely tidy.  It moves
    nothing: no stored purchase is dated after any reader's now -- the write
    guard bounds every row at ``display_today()``, which is never after the UTC
    ``date.today()`` a :class:`~app.services.balance_at.BalanceContext` pins by
    default, and zero rows in either database carry a future date (0 of 74 and 0
    of 47, re-verified 2026-07-26).  And the only read it could ever have
    changed is a HISTORICAL one, whose plan is TODAY's still-Projected rows
    clamped forward rather than the plan as it stood then -- so windowing their
    entries was a partial as-of purity inside a tier that has none.

    **The R-M re-ruling of 2026-08-01 did not bring that window back, and could
    not.**  ``settled_on`` -- the day the bank was seen to take the money -- MAY
    now be after today, because "I bought this and my bank has not taken it yet"
    is a true statement the app previously had no field to hold.  Such a
    purchase is simply not inside any asserted balance, so it lands in the
    OUTSTANDING bucket and raises the floor by exactly what it will cost.  That
    is a function of the row and the account, not of the reader's clock, so this
    rule stays clock-free.

    Args:
        txn: A Transaction object.  The ``entries`` relationship may
            be eager-loaded (canonical producer), unloaded
            (transitional caller; lazy-loads on demand), or absent
            (test fake).
        reconciled_through: The account's :class:`ReconciledThrough` boundary.
            Only consulted for a projected row carrying entries; every other
            arm returns ``effective_amount`` without reading it.

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

    # Partition the entries and hold back the unreconciled budget.  The
    # bucketing rule and the reservation formula live once, in
    # ``_entry_checking_impact`` (E-27).
    return _entry_checking_impact(
        entries, txn.estimated_amount, reconciled_through,
    )


def _credit_entry_sum(txn: Transaction) -> Decimal:
    """Return the sum of a transaction's credit (credit-card) entry amounts.

    The ``Sigma(credit entry amounts)`` term of the confirmed-cash-effect
    formula: an envelope's credit purchases are excluded from the checking
    outflow because each posts its own CC Payback when that payback settles
    (``credit_workflow``), so counting them here would double-count against the
    payback.  A plain transaction has no entries, so this is ``Decimal("0")``
    and the effect collapses to ``effective_amount``.

    Args:
        txn: The transaction whose credit entries to sum.

    Returns:
        The sum of ``amount`` over the transaction's ``is_credit`` entries, as a
        ``Decimal`` (``Decimal("0")`` when there are none).
    """
    return sum(
        (entry.amount for entry in txn.entries if entry.is_credit),
        Decimal("0"),
    )


def settled_cash_leg(txn: Transaction) -> Decimal:
    """Return the confirmed cash effect of a SETTLED row: what really moved.

    The settled counterpart of the projected valuations beside it, and the ONE
    statement of that rule: ``effective_amount - Sigma(credit entry amounts)``,
    signed ``+`` for income (money entering the account) and ``-`` for an
    expense (money leaving).  The sign follows the transaction TYPE, never the
    account class, so the leg is correct whether the cash account is an asset
    (Checking) or a liability (a direct charge on a Credit Card account).

    For a plain transaction the credit sum is zero and the effect collapses to
    ``+/-effective_amount``.  For an ENVELOPE at settle ``effective_amount``
    equals the sum of ALL its entries (``compute_actual_from_entries`` sets
    ``actual_amount`` so), and subtracting the credit entries collapses the
    result to the DEBIT-only outflow -- with no branch on "is this an envelope".

    **This is why the rule lives HERE (plan step X-a), not in the posting
    writer.**  It was ``posting_service._signed_cash_leg``, private to the
    module that WRITES the ledger -- the same inversion plan step B0 corrected on
    the loan side, where the payment split lived inside the posting package and
    every other consumer had to reach through its privates for it.  Two
    consumers need this rule now: the writer, which posts the effect, and the
    cash WALK (:func:`app.services.cash_ledger.walk_cash_ledger`), which folds
    it.  A second copy would let the projection and the posted ledger disagree
    about what a settled row was worth -- measured on production 2026-07-25
    before this move, a ``effective_amount``-only walk diverged from the posted
    ledger on 10 of 130 Checking rows and by up to ``$181.58`` on one, because
    every one of them was an envelope carrying credit-card entries.

    The bulk oracle reader ``posting_reads.settled_transaction_effect`` computes
    the same sum in SQL and deliberately stays independent: it is the Step-3
    reconciliation oracle's own window onto the ledger, and an oracle that
    shared this implementation could not grade it.

    **TOTAL: a non-contributing row is worth exactly zero.**  A soft-deleted or
    Credit / Cancelled row has an ``effective_amount`` of zero, but its ENTRIES
    survive on the row -- so without the guard below,
    ``0 - Sigma(credit)`` negated for an expense returns a FABRICATED INFLOW: a
    deleted grocery envelope carrying an $80.00 credit purchase valued at
    ``+$80.00``, money the account never received.  Unreachable through today's
    two callers (the walk pre-filters with
    :func:`~app.utils.balance_predicates.balance_contributing_clause`, and the
    writer resolves a target only on the settle side), which is exactly why it
    would have waited to be discovered by a third.  A function whose answer is
    correct only because every caller happens to pre-filter is a contract nobody
    can see; this one is total instead.

    Args:
        txn: The transaction whose confirmed cash effect to value.  A
            non-contributing row (soft-deleted, Credit, or Cancelled) returns
            ``0.00`` whatever entries it carries.

    Returns:
        The signed confirmed cash effect as a ``Decimal``.
    """
    if not is_balance_contributing(txn):
        return Decimal("0.00")
    net = txn.effective_amount - _credit_entry_sum(txn)
    return net if txn.is_income else -net


def income_amount(txn, basis: ProjectedBasis):
    """Return the income contribution for ``txn``, honoring a live override.

    Part of this module's public surface (no leading underscore): the seam's
    cash fold reaches it from another package, so the override seam resolves
    in one place rather than per reader.

    ``basis.amount_overrides`` is the live projected-net seam (Workstream B):
    a dict mapping transaction id -> Decimal produced by
    :func:`live_amount_overrides` below.  When the
    transaction's id is present, the live-recomputed net is used in
    place of the stored ``effective_amount`` so a projected salary
    paycheck reflects the current salary profile rather than a cached
    amount a later profile/calibration/code change may have invalidated.
    An empty map returns ``effective_amount`` unchanged.

    It takes the whole :class:`ProjectedBasis` although it reads only one of
    the two fields, and that is deliberate: the income and expense legs are
    handed the SAME object by the same reduction, so there is no shape in which
    one leg can be valued on a basis the other was not.  A signature that took
    only what it happens to need today would let a future reader thread the two
    facts separately, which is the "two producers that agree by coincidence"
    shape this module exists to prevent.

    Args:
        txn: An income Transaction.
        basis: The account's :class:`ProjectedBasis`.

    Returns:
        Decimal -- the override amount when present, else
        ``txn.effective_amount``.
    """
    override = _override_for(txn, basis.amount_overrides)
    return txn.effective_amount if override is None else override


def _expense_amount(txn, basis: ProjectedBasis):
    """Return the expense contribution for ``txn``, honoring a live override.

    The expense-leg analogue of :func:`income_amount`.  When the
    transaction's id is in ``basis.amount_overrides`` (the live-derive seam --
    e.g. a recurring loan-payment transfer whose cash debit is derived
    from the destination loan via
    :func:`app.services.loan_payment_service.live_loan_transfer_amounts`),
    the live amount replaces the stored figure.  Otherwise it falls back
    to :func:`_entry_aware_amount`, preserving the entry-checking formula
    for envelope expenses.  An empty override map returns the entry-aware
    amount unchanged.

    An override WINS over the entry formula: a live-derived amount is what
    the row is worth now, and it carries no entries to reduce.

    Args:
        txn: An expense Transaction.
        basis: The account's :class:`ProjectedBasis`.

    Returns:
        Decimal -- the override amount when present, else
        :func:`_entry_aware_amount`.
    """
    override = _override_for(txn, basis.amount_overrides)
    if override is not None:
        return override
    return _entry_aware_amount(txn, basis.reconciled_through)


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
