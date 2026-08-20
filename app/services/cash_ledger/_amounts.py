"""
Shekel Budget App -- Cash ledger: what ONE row is WORTH to checking.

The per-transaction valuation rules, and nothing that sums or folds them.  Given
a single row, these answer "how much of this hits the checking balance right
now?" -- the cash analog of :mod:`app.services.loan_ledger._split`, which
answers the same question for one loan payment (principal / interest / escrow).

**The VALUATION is here and the AMOUNT is next door.**  :mod:`._amount_source`
answers *what is this row's amount* -- the quantity the amount column holds or
would hold, by ruling R-FI's five rules.  This module answers *what is it
worth*, which composes that amount with an entered actual, an excluded status, a
soft delete and an envelope's purchases.  :func:`contributed_amount` is that
composition in one function and it took over from
``Transaction.effective_amount`` at plan step X-au-c2: a model property could not
resolve a derived amount -- it is a pure in-memory read, and a paycheck's
derivation needs the owner's whole pay-period set -- so the figure arrives as an
ARGUMENT a caller cannot forget.  :func:`contributions_by_id` is the batch a
reader with a row set uses, and
:func:`~app.services.row_valuation.owned_contribution` is the cheap accessor
for a reader that can only ever see rows owning their figure.  The BUDGET twins
of those two are :func:`._amount_source.amounts_by_id` and
:func:`~app.services.row_valuation.owned_amount`, which answer what a row's
amount IS rather than what it is worth (ruling E-21, plan step X-au-c2b).

**The arms that need no producer live one module DOWN, in
:mod:`app.services.row_valuation`** (plan step X-au-c2).  ``fixed_contribution``
(the status / soft-delete / entered-actual gate), ``own_figure`` (the NULL
refusal) and the pair ``owned_amount`` / ``owned_contribution`` are pure per-row
reads.  The loan stack
needs the last of them and cannot name this package without raising pylint's
``cyclic-import``, because :mod:`._amount_source` reaches UP into
``loan_payment_service`` for rule 4's producer; that module's docstring carries
the measurement.  Of the four, ``owned_amount`` and ``owned_contribution`` are
re-exported from this package (``__init__``'s ``__all__``) -- ``fixed_contribution``
is imported here for the valuations below and ``own_figure`` only by
``owned_amount`` itself, both as internal uses rather than public surface.  There is
still exactly ONE definition of each rule, which is the claim this module
exists to make.  What is genuinely inverted is that upward reach, and plan step
X-au-g owns unwinding it.

Three rule families live here, split by the question they answer.

What a row is worth while it is still PROJECTED -- a reservation, money not yet
gone -- is two of them, composing in one direction:

  * :func:`live_override` asks the basis's two live DERIVATIONS what supersedes
    the row's stored figure; it IS the read-time repair ruling R-FI deletes, and
    the per-kind cutovers take it with the producers it merges; and
  * :func:`income_amount` / :func:`_expense_amount` CONSUME that lookup, falling
    through to the valuation -- and, for an expense carrying entries, to the
    entries-aware reservation formula :func:`_entry_checking_impact`.

What a row is worth once it has SETTLED -- money that really moved -- is the
third, and it is deliberately none of the above:

  * :func:`._cash_leg.settled_cash_leg` is ``owned_contribution - Sigma(credit entries) -
    Sigma(posted purchases)``, signed by transaction type.  Neither read-time
    adjustment above can reach a settled row (both filter to ``is_projected``),
    and a reservation would be meaningless for cash already gone.  It arrived
    here at plan step X-a from ``posting_service``, so the ledger WRITER and the
    cash WALK price one row through the same function.  Its third term is ruling
    **R-FM** (plan step X-f3b): a purchase whose bank posting day is recorded
    books its own cash leg on its own day, so the envelope's close books only
    what its purchases did not.

**Why they are one module (plan step D1c).**  They were split across two: the
override map's producer sat in the cash event sources while the four rules that
read its output sat inside ``balance_calculator``, a PRODUCER module, where
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

# ``owned_amount`` is imported for the package's public surface rather than for
# this module's own use: ``__init__`` re-exports the pair from here so a reader
# takes both budget accessors from one place (see the module docstring).
# Pylint: ``unused-import`` -- the re-export IS the use; ``__init__`` names it.
from app.services.row_valuation import (  # pylint: disable=unused-import
    fixed_contribution,
    owned_amount,
    owned_contribution,
)
from app.utils.balance_predicates import is_projected

from ._amount_basis import AmountBasis
from ._amount_source import (
    amounts_by_id,
    resolve_transaction_amount,
)


@dataclass(frozen=True)
class ReconciledThrough:
    """The day through which an account's movements are inside a declared balance.

    Ruling R-DH (a): an assertion is the CLOSING balance for its civil day, so
    a movement dated at or before that day is inside it by definition.

    **It answers for the MODELLED side now, and that narrowing is plan step
    X-f3a-1's** (ruling **R-FL**).  This was "the ONE statement of the question
    this whole arc turns on", and for a CASH line it no longer is: whether the
    bank showed a transaction or a purchase is a RECORDED fact, asked through
    :class:`~._clearing.StatementCoverage`, because the developer's own bank
    exports measured the date comparison wrong on 70% of matched movements.
    What is left here is the case R-FL calls not an exception -- a payroll
    contribution (R-Z) and a modelled accrual (R-L / R-Y) are not lines anyone
    can tick, so the assertion legitimately outranks the model and the question
    really is "is this payday after the latest assertion" -- plus the posted
    self-heal's cost guard, which grades journal-entry DATES rather than lines
    and says so at its own site.

    An adversarial review once found a seventh statement of the rule hiding
    behind a loose date (``balance_at._asset_contributions``: a payroll
    contribution on a payday the assertion already covers is money the asserted
    balance contains, and modelling it again double counts); that consumer is
    one of the two this type now exists for.

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
    ``docs/audits/balance_architecture/archive/anchor_settle_partition.md`` was
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
    is a fact about money: ``Transaction.paid_at`` WAS ``db.func.now()`` at the
    click (deleted at plan step X-f1) and an assertion's ``created_at`` is
    when it was typed.  So the
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
    (``reconcile_service.record_settled_days``) and which a bank import would do
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

        **Totality is what the remaining callers need, and the paragraph that
        used to be here described callers this type no longer has.**  It
        explained how a ``False`` behaved inside the two walks' ``while`` loops
        -- loops plan step X-f3a-1 deleted, along with every cash caller of this
        method.  What asks it now is the modelled contribution feed (R-Z), for
        which a ``None`` on either side means "not inside any declared balance",
        which is the answer wanted and needs no precondition remembered.

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


def live_override(txn, basis: AmountBasis):
    """Return ``txn``'s live override amount, or ``None`` when it has none.

    The one override lookup both valuation legs share.  ``income_amount`` and
    :func:`_expense_amount` each carried an identical four-line copy of it --
    harmless while they sat in a producer module, but this module's whole claim
    is that the per-row rules have ONE home, and two copies of the seam's
    entry condition is the shape that claim exists to prevent.

    **This IS the read-time repair ruling R-FI deletes, and it is scheduled
    rather than kept.**  A salary row and a derive-mode loan shadow both store a
    figure the app also computes, so every balance surface shows the live
    recompute while the column behind it goes stale (finding **N-224**).  The
    per-kind cutovers end that by declaring those rows DERIVED -- at which point
    the resolver answers them from the same producers and this lookup has
    nothing left to find, so plan steps X-au-d (salary) and X-au-g (loan) delete
    it with the producers it merges.  Until then it stays exactly where it was
    and outranks everything below it, because moving it would change what a
    balance says before the schema change that makes the new answer structural.

    The two halves are asked HERE rather than merged in the basis: which rule
    prices a row is a fact about the row, never about which map its id turned up
    in (ruling R-FI's refuted discriminator).

    **It asks each derivation directly since plan step X-au-c2b**, where it used
    to index a map :func:`live_amounts` had built for a whole row set.  The LOAN
    half is asked first, preserving the precedence the flattened
    ``{**salary_net, **loan_cash}`` had -- **and that precedence is now
    UNREACHABLE, which an adversarial review of this step established.**  The
    loan half needs a ``transfer_id`` and the salary half a ``template_id``, and
    ``ck_transactions_one_pricing_link`` admits at most ONE of the three pricing
    links per row (plan step X-au-c1, measured at 0 violations over 997 rows).
    So the two candidate sets are disjoint by CONSTRAINT, where the paragraph
    this replaces said they rested on a convention.  The order is kept because
    an unreachable branch written in the safe order costs nothing and stating it
    is how the next reader learns the constraint is what makes it unreachable --
    but no test can construct the collision, and none pretends to.

    Each half answers ``None`` from the row's own columns before it touches its
    derivation, so a row set holding neither kind resolves nothing and issues no
    query.

    Args:
        txn: The Transaction being priced.
        basis: The read pass's :class:`~._amount_source.AmountBasis`.

    Returns:
        The override ``Decimal`` when either derivation answered for ``txn``,
        else None.
    """
    # Pylint: ``import-outside-toplevel`` -- the paycheck / tax and
    # loan-resolver stacks stay off this module's load path, the same reason
    # ``_amount_source.amount_basis`` imports them at call time (finding N-267).
    # pylint: disable=import-outside-toplevel
    from app.services.income_service import live_projected_net
    loan = basis.loans.live_cash(txn)
    if loan is not None:
        return loan
    return live_projected_net(txn, basis.salary)


def live_amounts(basis: AmountBasis, rows) -> dict[int, Decimal]:
    """Return both live derivations' answers over *rows* as ONE map.

    The map form of :func:`live_override`, and it delegates to that per-row
    question rather than restating the merge -- which is what keeps the two from
    coming to disagree about precedence.  It exists for the surfaces that want a
    LOOKUP rather than a per-row question: the grid publishes it so a cell and
    the balance row beside it read one object (ruling R-Q), and the period
    figures carry it for the same reason.

    **It takes the rows it is a map OVER** (plan step X-au-c2b).  It used to
    flatten two ``{transaction_id: Decimal}`` maps the basis carried, which is
    why the basis had to be built per row set at all; a basis now holds the
    derivations, and a map keyed by row id is something a caller asks for about
    ITS OWN rows.  Only the rows a live derivation answers for appear, so the
    result is still the "is there a live figure for this id" lookup its
    consumers read it as.

    Args:
        basis: The read pass's :class:`~._amount_source.AmountBasis`.
        rows: The loaded rows to build the map over.

    Returns:
        ``{transaction_id: Decimal}``, empty when no row has a live figure.
    """
    answers: dict[int, Decimal] = {}
    for txn in rows:
        live = live_override(txn, basis)
        if live is not None:
            answers[txn.id] = live
    return answers


def display_amounts_by_id(rows, basis: AmountBasis) -> dict[int, Decimal]:
    """Return ``{transaction_id: the figure a SCREEN shows}`` for *rows*.

    **The ONE rule for what a row's amount displays as** (plan step X-au-c2b):
    what its amount RESOLVES to (:func:`~._amount_source.amounts_by_id`),
    superseded by a live recompute where one exists (:func:`live_amounts`).
    Two questions in one answer, and the composition is the answer -- which is
    why it is a function rather than two lines each caller writes.

    **It exists because an adversarial review found the two lines written
    twice, differently.**  The grid published the resolved map with the seam's
    override map laid over it; every HTMX fragment and the companion view
    published the resolved map ALONE, under the same context key.  So a
    projected salary row whose profile had moved past its cached column showed
    the live net on the grid and the stale column in the quick-edit box the same
    click opened -- and that box is the one a save posts back from.  Three
    surfaces, three answers, which is finding **N-224**'s shape reproduced by
    the very step that set out to give a row ONE figure.

    **The override half is TRANSITIONAL and dies with the cutovers.**  Ruling
    R-FI deletes the read-time repair: once X-au-d and X-au-g declare their rows
    DERIVED the resolver answers them from the same producers, :func:`live_amounts`
    finds nothing, and this collapses into :func:`~._amount_source.amounts_by_id`
    on its own.  Until then a screen must show the repaired figure, because that
    is what every balance surface already folds (ruling **R-Q**).

    Args:
        rows: The rows a surface is about to render.
        basis: The read pass's :class:`~._amount_source.AmountBasis`.

    Returns:
        ``{transaction_id: Decimal}`` covering every row.

    Raises:
        AmountUnresolvable: From the resolver, for a row whose rule cannot
            answer.  A refusal is never a fallback.
    """
    displayed = amounts_by_id(rows, basis)
    displayed.update(live_amounts(basis, rows))
    return displayed


def contributed_amount(txn, resolved: Decimal) -> Decimal:
    """Return what *txn* contributes to a balance, given its RESOLVED amount.

    **The replacement for ``Transaction.effective_amount``** (plan step
    X-au-c2), and the argument is the whole difference.  That property was a
    pure in-memory read with four arms, the last of which was "the stored
    ``estimated_amount``" -- and under the amount model a derived row stores
    none, so it could not answer at all (ruling **R-FI**).  It could not be
    taught to: resolving a paycheck needs the owner's whole pay-period set,
    because the biweekly rounding residue only reconciles against the complete
    annual figure, so no per-row property can hold the derivation.

    Taking the figure as an ARGUMENT is what a caller cannot forget.  A property
    is free to read and answers whatever it can; this signature does not exist
    without a resolved figure, and the only way to get one for a row set is
    :func:`contributions_by_id`, which builds the basis the resolver requires.

    Args:
        txn: The row being valued.
        resolved: What its amount resolves to
            (:func:`~._amount_source.resolve_transaction_amount`), or the
            reader's own live figure where one supersedes it.

    Returns:
        ``0`` for a row that contributes nothing, what the row RECORDED as
        having moved once it has settled, else *resolved*.
    """
    fixed = fixed_contribution(txn)
    return resolved if fixed is None else fixed


def contributions_by_id(rows, basis: AmountBasis) -> dict[int, Decimal]:
    """Return ``{transaction_id: what the row contributes}`` for *rows*.

    **The BATCH valuation every reader that can see a still-projected row uses**
    (plan step X-au-c2).  It values each row through
    :func:`~app.services.row_valuation.fixed_contribution`, resolving only the
    rows that reach the amount model at all -- so the paycheck engine runs once
    per read pass rather than once per row (finding **N-228**).

    **It TAKES the basis rather than building one** (plan step X-au-c2b).  A
    basis is pinned to an owner and a scenario, not to a row set, so a caller
    that also needs the budget map or the live-override map asks all of them
    against ONE -- which is how a single request stopped running the paycheck
    engine once per row set (findings **N-268**, **N-269**).  A caller with a
    read pass passes ``ctx.amounts()``; one without builds its own with
    :func:`~._amount_source.amount_basis`.

    That ordering is the point rather than an optimisation: an excluded row is
    worth ``$0.00`` and its derived amount has no producer to answer it, so a
    valuation that resolved first and gated afterwards would 500 on a row nobody
    is counting.

    Every id in *rows* appears in the result, so a caller indexes it with ``[]``
    and a row it forgot to price raises a ``KeyError`` where it is read.  There
    is deliberately no ``.get(id, default)`` shape: a default here is a
    fabricated figure in a money path.

    Args:
        rows: The loaded rows to value.  They may span accounts -- the basis is
            keyed on the owner, not on one account -- but every one must belong
            to *basis*'s owner and scenario.
        basis: The read pass's :class:`~._amount_source.AmountBasis`.

    Returns:
        ``{transaction_id: Decimal}`` covering every row.

    Raises:
        AmountUnresolvable: From the resolver, for a row whose rule cannot
            answer.  A refusal is never a fallback (see
            :mod:`app.services.cash_ledger._amount_source`).
    """
    return {row.id: contribution_of(row, basis) for row in rows}


def contribution_of(txn, basis: AmountBasis) -> Decimal:
    """Value one row against *basis*, resolving it only if it needs resolving.

    The per-row body of :func:`contributions_by_id`, split out so the gate and
    the resolve are one expression rather than a comprehension with a
    conditional in it.

    Args:
        txn: The row being valued.
        basis: The basis :func:`contributions_by_id` built over its whole set.

    Returns:
        What the row contributes.
    """
    fixed = fixed_contribution(txn)
    if fixed is not None:
        return fixed
    return resolve_transaction_amount(txn, basis)


def _entry_checking_impact(entries, estimated_amount: Decimal) -> Decimal:
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

        posted_debit   = sum(debit amounts the bank has been seen to take)
        unposted_debit = sum(debit amounts it has not)
        sum_credit     = sum(amount where is_credit)

        impact = max(estimated_amount - posted_debit - sum_credit,
                     unposted_debit)

    A POSTED debit has already left the account and is already a cash movement
    of its own in the ledger (ruling **R-FM**), so it is subtracted from the
    reservation.  An UNPOSTED debit acts as a floor -- the reservation can never
    be smaller than the checking hits nothing has recorded yet, which also
    handles overspend.  A credit entry never hits checking directly (it flows
    through a CC Payback sibling transaction), so it only reduces the
    reservation and its own dates are irrelevant.

    **Which bucket a debit falls in is whether it has POSTED, and that is
    ruling R-FM** (plan step X-f3b).  Its history is the point, because this
    bucket has been re-decided three times and each move was a narrowing.  It
    was a stored ``is_cleared`` boolean, written by a bulk UPDATE at every
    anchor true-up over "every entry dated on or before the SERVER's today" --
    so a purchase recorded BEFORE the true-up was reconciled and the identical
    purchase recorded after it never was, and the difference was which button
    the user pressed first.  Ruling R-DH (d) deleted the boolean and derived it
    from ``settled_on`` against the account's latest asserted day; ruling R-FL
    replaced that derivation with the RECORDED fact of which statement showed
    the line, because the developer's bank exports falsified the date compare on
    70% of matched movements.

    **R-FM ends the question here entirely**, and that is the simplification
    rather than a fourth answer.  While a purchase was not a cash movement, the
    reservation had to ask whether a declared balance already contained it --
    because that was the only way its money could be in the book at all.  Now a
    purchase carrying a posting day IS in the book, on its own day, and WHICH
    statement cleared it is the walk's question about that movement
    (:class:`~._clearing.StatementCoverage`) exactly as it is for a settled
    transaction.  So this reduction asks one fact about the row in front of it
    and no fact about the account, and it cannot come to disagree with the
    clearing rule because it no longer states one.

    A purchase whose posting day has never been recorded is UNPOSTED -- the
    conservative arm: the envelope keeps holding its whole budget back until the
    user confirms the money has actually left.  Nothing here guesses a posting
    day on the user's behalf.

    This function sees whatever entry set it is handed and applies the
    bucketing to all of it.  Short-circuiting an empty set belongs to the
    caller, and there is exactly one, so that decision is made once rather than
    kept in step across two paths.

    Args:
        entries: An iterable of entry rows, each exposing ``settled_on``
            (``date | None``), ``amount`` (Decimal) and ``is_credit`` (bool).
            The caller is responsible for short-circuiting an empty sequence
            before calling.
        estimated_amount: Decimal -- the transaction's budgeted amount,
            the reservation ceiling before debits and credits reduce it.

    Returns:
        Decimal -- the amount this transaction's entries hold back from
        the checking balance.
    """
    posted_debit = Decimal("0")
    unposted_debit = Decimal("0")
    sum_credit = Decimal("0")
    for entry in entries:
        if entry.is_credit:
            sum_credit += entry.amount
        elif entry.settled_on is not None:
            posted_debit += entry.amount
        else:
            unposted_debit += entry.amount

    return max(
        estimated_amount - posted_debit - sum_credit,
        unposted_debit,
    )


def _entry_aware_amount(txn, basis: AmountBasis) -> Decimal:
    """Compute the checking-balance impact for a single expense transaction.

    For projected expenses with entries (loaded eagerly or
    lazy-loaded on demand), the formula partitions debit entries into
    posted and unposted buckets, then holds back only the portion
    of the budget that has not already left the account:

        posted_debit   = debits carrying a recorded bank posting day
        unposted_debit = every other debit
        sum_credit     = sum(entries where is_credit)

        checking_impact = max(
            estimated_amount - posted_debit - sum_credit,
            unposted_debit,
        )

    Semantics:
      - A POSTED debit has already left the account and is already a cash
        movement of its own in the ledger (ruling **R-FM**, plan step
        X-f3b), so it must not come out of the projection a second time --
        we subtract it from the reservation.
      - An UNPOSTED debit has not been seen to leave, so the full
        estimated amount must still be held back (the max() floor
        handles this and also handles overspend where unposted
        debits exceed the remaining reservation).
      - A credit entry never hits checking directly -- it flows through
        a CC Payback sibling transaction -- so it only reduces the
        reservation, whatever its dates say.
      - With every ``settled_on`` NULL (the state a fresh purchase is in,
        and the state migration ``d7c1f4a9e603`` left every existing row
        in), posted_debit = 0 and the formula reduces to
        max(estimated - sum_credit, unposted_debit) -- the whole budget
        held back, which is the conservative arm and matches the
        pre-cleared-flag behavior from scope doc section 4.2.

    Example (the user's grocery bug):
      est = 500, three debit purchases summing to 462.34, all confirmed
      against a statement whose balance the user then entered.
      checking_impact = max(500 - 462.34 - 0, 0) = 37.66, which is the
      remaining budget to hold back now that the ledger carries the
      first three purchases as movements of their own.

    **The two halves always sum to what the row costs**, which is the
    property ruling R-FM turns on: the posted debits are in the ledger at
    their own days, this reservation holds the rest, and the envelope's
    close books ``sum(entries) - credit - posted_debit``
    (:func:`._cash_leg.settled_cash_leg`).  So recording a purchase and truing the
    anchor up by the same amount still cannot move the projected end
    balance (ruling R-DH (c)) -- and it stops depending on the anchor RESET
    dropping the balance by exactly what the reservation released, which is
    what finding **N-274** measured and what the cutover (X-f3c) removes.

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
    :func:`._cash_leg.settled_cash_leg` beside it already is; the reader's clock decides
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

    **The R-M re-ruling of 2026-08-01 did not bring that window back, and plan
    step X-f3b is what keeps it out.**  ``settled_on`` -- the day the bank was
    seen to take the money -- was unbounded ABOVE while a purchase was not a
    cash movement, because a future day was then the conservative direction: the
    purchase stayed outstanding and the whole budget stayed reserved.  Ruling
    **R-FM** inverts that, and the inversion is exactly the shape
    ``status_seam.reject_future_settle_day`` refuses on a transaction: a future
    posting day would release the reservation NOW and book the money later,
    putting already-spent money back into today's projection.  So the bound
    moved to the write door
    (:func:`app.services.entry_service._reject_future_posting_day`) rather than
    a clock arriving here.  A purchase the bank has not taken yet leaves the day
    NULL, which is what that state has always meant.

    **The budget this holds back is the RESOLVED amount, not the stored column**
    (plan step X-au-c2).  ``estimated_amount`` is what an envelope's budget was
    read from until the amount model made it NULL for a derived row, and an
    envelope generated by a recurring definition is exactly such a row once the
    template cutover (X-au-e) lands -- so the ceiling the three buckets reduce is
    :func:`~._amount_source.resolve_transaction_amount`'s answer, which for a row
    that owns its figure IS that column.  It is the same figure
    :func:`contributed_amount` would return for the row absent entries, so the
    two arms of this function cannot come to price one row two ways.

    Args:
        txn: A Transaction object.  The ``entries`` relationship may
            be eager-loaded (canonical producer), unloaded
            (transitional caller; lazy-loads on demand), or absent
            (test fake).
        basis: The account's :class:`~._amount_source.AmountBasis` -- the amount
            basis every arm prices through.  It carried the account's
            :class:`~._clearing.StatementCoverage` beside it until plan step
            X-f3b, in a ``ProjectedBasis`` wrapper; the reservation is the
            fact that needed it, and it no longer asks (see
            :func:`_entry_checking_impact`), so the wrapper went with it.

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
    # load-bearing, not stylistic: ``is_projected`` resolves a ``ref_cache`` id,
    # which is work an entry-less row has no reason to pay for.
    #
    # **The reason it used to give was that ``is_projected`` raises on a non-ORM
    # fake, and plan step X-au-c3 voided it**: every valuation now asks the
    # STATUS whether a row is worth what it RECORDED or what it PLANS, so a row
    # carrying no ``status_id`` is one no valuation could ever see.  The
    # ordering is graded by a call-counting spy instead of by a missing
    # attribute, and that spy still fails when the two guards are swapped --
    # verified by making the swap.
    if not entries:
        return contribution_of(txn, basis)

    # Only apply the entry formula to projected transactions.
    # Settled, cancelled, and credit statuses are already handled
    # correctly by the valuation (zero for excluded statuses,
    # actual_amount for settled statuses).  Routed through the
    # centralized ``is_projected`` predicate (D6-09 / MED-02) so
    # this entry-formula gate cannot drift from the other
    # Projected-only filters in this package and in the balance
    # resolver.
    if not is_projected(txn):
        return contribution_of(txn, basis)

    # Partition the entries and hold back the unposted budget.  The
    # bucketing rule and the reservation formula live once, in
    # ``_entry_checking_impact`` (E-27).
    return _entry_checking_impact(
        entries, resolve_transaction_amount(txn, basis),
    )


def income_amount(txn, basis: AmountBasis):
    """Return the income contribution for ``txn``, honoring a live override.

    Part of this module's public surface (no leading underscore): the seam's
    cash fold reaches it from another package, so the override seam resolves
    in one place rather than per reader.

    Three answers in one expression, in precedence order.  The live override
    (:func:`live_override`) wins where one exists, so a projected salary
    paycheck reflects the current salary profile rather than a cached amount a
    later profile, calibration or code change may have invalidated; absent one
    the row is valued through the amount model
    (:func:`contribution_of`) -- ``0`` for a row that contributes
    nothing, what a SETTLED row recorded as having moved, else what the row's
    amount RESOLVES to.

    **The override outranks the amount model here, preserved rather than ruled**
    (plan step X-au-c2), and unreachable on production: the override map's
    producers take Projected rows only, and a Projected row records nothing.
    X-au-d deletes the arm outright.  This paragraph also named a SECOND
    precedence -- ``_freshest_amount`` refusing to refresh a row carrying an
    ``actual_amount`` -- which plan step X-au-c3 deleted with the column that
    made a machine's figure and a human's indistinguishable.

    **It takes the ``AmountBasis`` directly since plan step X-f3b**, where it
    took a ``ProjectedBasis`` wrapper before.  That record bundled TWO
    per-account facts so they could not be threaded apart -- the amount basis
    and the account's clearing rule -- and ruling **R-FM** removed the second:
    the reservation asked it, and a purchase's posted-ness is now a fact about
    the purchase.  A one-field wrapper carries nothing a caller could get wrong,
    so it went rather than surviving as a shape whose docstring named a field it
    no longer had.

    Args:
        txn: An income Transaction.
        basis: The account's :class:`~._amount_source.AmountBasis`.

    Returns:
        Decimal -- the override amount when present, else the row's
        contribution against the basis.
    """
    override = live_override(txn, basis)
    if override is not None:
        return override
    return contribution_of(txn, basis)


def _expense_amount(txn, basis: AmountBasis):
    """Return the expense contribution for ``txn``, honoring a live override.

    The expense-leg analogue of :func:`income_amount`.  When a live producer
    answered for the row -- e.g. a recurring loan-payment transfer whose cash
    debit is derived from the destination loan via
    :meth:`~app.services.loan_payment_service.LoanPricing.live_cash` -- the
    live amount replaces every other answer.  Otherwise it falls to
    :func:`_entry_aware_amount`, preserving the entry-checking formula for
    envelope expenses.

    An override WINS over the entry formula: a live-derived amount is what
    the row is worth now, and it carries no entries to reduce.

    Args:
        txn: An expense Transaction.
        basis: The account's :class:`~._amount_source.AmountBasis`.

    Returns:
        Decimal -- the override amount when present, else
        :func:`_entry_aware_amount`.
    """
    override = live_override(txn, basis)
    if override is not None:
        return override
    return _entry_aware_amount(txn, basis)
