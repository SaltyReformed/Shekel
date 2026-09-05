"""
Shekel Budget App -- What ONE row is worth, without asking any producer.

The pure half of the cash valuation: the arms that answer from the row itself
-- a status that excludes it, a soft delete, the SETTLEMENT it recorded, and
the figure the row OWNS.  Nothing here consults the amount model's live
producers, which is the whole reason it is a module of its own.

**The settlement arm is what makes the two halves total between them** (plan
step X-au-c3).  A row is a PLAN until its money moves and a RECORD of what moved
once it has, and the two never share a column: :func:`settled_figure` answers
from the record, :func:`owned_amount` answers from the plan, and a row is in
exactly one of the two states.  Before that split a settled row's record was
optional, so most settled rows fell through to the plan -- and since the plan is
a derivation, the plan had to be frozen at settle to stop a later price change
moving a figure the bank had already taken.

**It was made a LEAF because the loan stack could not NAME the cash-ledger
package, and the gate that forbade it was pylint's** (plan step X-au-c2).
:mod:`~app.services.cash_ledger._amount_source` reached UP into
``loan_payment_service`` for amount rule 4's producer (``LoanPricing``).  That
import was DEFERRED to call time, so no ``import`` STATEMENT closed a loop --
but ``cyclic-import`` (R0401) traces function-level imports too, so a
module-level ``from app.services.cash_ledger import ...`` anywhere reachable
from ``loan_payment_service`` closed the cycle for pylint.

*This paragraph used to add that "importing ``app.services.cash_ledger``
pulled in no loan service at all -- that much an adversarial review measured".
It is DELETED rather than tensed, because it is false and was false when it
was written: ``app/__init__.py`` is the application factory and imports the
tree, so ``import app.services.cash_ledger`` puts 264 app modules in
``sys.modules`` -- ``loan_payment_service`` among them -- on the pre-move tree
and the post-move one alike (measured 2026-08-31, identical both sides).  What
was true is the narrower claim the sentence above now makes: no import
statement in this package NAMED a loan service at module level.*

**THE REACH IS DELETED as of plan step X-au-g-2a, and this paragraph is kept in
the PAST TENSE rather than left standing as a live reason.**  That step moved
rule 4's producer DOWN into ``cash_ledger`` (``_loan_installment`` /
``_loan_pricing``), which is the unwind the paragraph below has always said
``X-au-g`` owes: the amount model is the lower tier and was asking a loan
service to price a row.  What that package names now are loan TERM primitives
-- ``loan_loaders``, ``loan_resolver``, ``escrow_calculator``,
``recurring_transfer_query`` -- none of which names ``cash_ledger``, so the
loan READING stack is free to import it, and plan step X-au-g-2c SPENT that
freedom: ``loan_payment_service.get_payment_history`` prices its feed through
``cash_ledger.contributions_by_id`` now, not through the accessor below.

**So this module's split is no longer FORCED, and saying so is the point of
keeping the history.  What survives is the SEAM; its ADDRESS is now
arbitrary.**  The arms here answer from the row alone and consult no producer,
which is a real distinction whichever tier can reach it -- but that argues for
a seam, not for a separate top-level module: ``cash_ledger._amounts`` and
``cash_ledger._amount_source`` are already two modules for exactly this
producer / no-producer split, inside one package.  Its registry scope stands
on separate ground
(``shekel_checkers/_fence_rulings._ROW_VALUATION_MODULES`` -- extracting a
fenced module's contents into an unfenced neighbour is finding N-28's shape,
which never depended on the cycle).  Whether it should FOLD BACK into
``cash_ledger`` now that it can is a question X-au-g-2a deliberately did not
take -- a second change, with its own diff and its own review -- and silence
here is not an answer to it.  There is still exactly ONE definition of each
rule, which is the claim :mod:`app.services.cash_ledger._amounts` exists to
make; it simply lives in a file both tiers can reach.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01): ORM rows in,
``Decimal`` out; no Flask import, no writes, and no query this module ISSUES.
:func:`settled_figure` reads the ``entries`` relationship for a
``purchases``-basis row, which lazy-loads if a caller did not eager-load it --
the same access :func:`~app.services.cash_ledger.settled_cash_leg` already
makes, and the fold's own loader issues ``selectinload(Transaction.entries)`` so
no routed caller pays for it per row.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import SettlementBasisEnum
from app.exceptions import AmountUnresolvable
from app.utils.balance_predicates import (
    is_balance_contributing,
    settled_status_ids,
)


def purchases_total(entries) -> Decimal:
    """Return the sum of a row's purchases -- ALL of them, debit and credit.

    The figure a ``purchases``-basis settlement records
    (:class:`app.enums.SettlementBasisEnum`), and the one statement of it.  Both
    kinds of entry count: the credit portion leaves the account through its CC
    Payback sibling rather than through this row, and
    :func:`~app.services.cash_ledger.settled_cash_leg` is what subtracts it,
    so removing it here would take it out twice.

    **It lives HERE rather than in ``entry_service``, where it was
    ``compute_actual_from_entries``** (plan step X-au-c3).  Two reasons, and the
    first is mechanical: :func:`settled_figure` below needs it, this module sits
    under both the cash and loan tiers, and ``entry_service`` reaches down
    through ``posting_service`` into ``cash_ledger`` -- so importing it here
    would close a cycle.  The second is that the old name referred to
    ``actual_amount``, a column this step removed; a helper named for a column
    that no longer exists is a citation a reader cannot follow.

    Pure: no database access, no ORM attribute beyond each entry's ``amount``.

    Args:
        entries: The row's :class:`~app.models.transaction_entry.TransactionEntry`
            rows, in any order.

    Returns:
        Their summed ``amount`` as a ``Decimal`` -- ``Decimal("0")`` for an
        empty sequence, which is the "no purchases recorded" answer rather than
        a missing one.
    """
    return sum((entry.amount for entry in entries), Decimal("0"))


def settled_figure(txn) -> "Decimal | None":
    """Return what *txn* RECORDS as having moved, or ``None`` if it has not settled.

    **The one accessor for the settlement record** (plan step X-au-c3), and the
    reason the amount model needs no freeze.  A row is a PLAN --
    ``estimated_amount`` priced by ``amount_source_id`` -- until its money moves,
    and once it has, it also carries a RECORD: the day, the figure, and how that
    figure is known.  Every money reader of a settled row asks this and never
    the plan, so a definition's price series may gain a version dated into the
    past -- which it legitimately does, because a series records what a price
    WAS -- without moving a figure the bank already took.

    The three bases (:class:`app.enums.SettlementBasisEnum`) answer two ways:

      * ``derived`` and ``corrected`` STORE the figure in ``settled_amount``,
        because neither is re-derivable -- the app's resolution at the moment of
        settle is a point in time, and a human's reading of a statement is not
        computable at all;
      * ``purchases`` stores NOTHING and sums the row's entries, because those
        entries are themselves the records.  A stored copy would need a
        reconciler to keep it in step with its own children, which is the shape
        ruling **R-FI** deletes.

    **THE STATUS DECIDES, NOT THE COLUMNS** (plan step X-au-c3, developer
    2026-08-17).  A row that has been reverted out of the settled band still
    CARRIES what it recorded -- a revert releases the assertion (``settled_on``
    and the clearing link) and keeps what moved, so the revert / edit /
    re-settle round trip the full-edit popover instructs the user to perform
    does not destroy a figure they read off a statement
    (``status_seam.apply_status_change``).  What keeps that retained figure out
    of every balance is this gate: a row that is not settled is worth its PLAN,
    whatever it still remembers.

    Reading ``settled_basis_id IS NOT NULL`` here instead would be the same
    question answered by the wrong column -- and it is the reason a first
    version of this step had to DESTROY the figure on a revert, because with the
    valuation inferring settled-ness from the record, the record had to go for
    the inference to come out right.  A CHECK constraint was written to enforce
    that pairing.  Deleting the inference deleted the constraint, the release
    and the data loss together.

    **It refuses rather than answering ``None`` for a broken record.**  A
    ``derived`` or ``corrected`` settlement with no stored figure has
    ``ck_transactions_settled_amount_needs_basis`` intact and the write-door rule
    broken -- the one half of the pairing a CHECK cannot state, because saying it
    needs the constraint to name a ref id.  Answering ``None`` there would send
    the caller to the row's PLAN, which is the exact fallback this step exists to
    remove, and it would do it silently.

    Args:
        txn: The row being asked.  ``status_id`` is read first, so a row that
            has not settled costs one frozenset membership test; the ``entries``
            relationship is read only for a SETTLED ``purchases``-basis row.
            Callers that value many rows should eager-load it --
            ``cash_ledger._facts._unwindowed_contributing_rows``,
            ``routes/grid/page``, ``spending_analysis.query_settled_expenses``,
            ``query_settled_expenses_in_span`` and
            ``savings_dashboard_service._metrics`` all issue
            ``selectinload(Transaction.entries)`` for that reason.

    Returns:
        The recorded figure, or ``None`` when this row is not settled.

    Raises:
        AmountUnresolvable: When the row records a basis that stores a figure
            and stores none.
    """
    if txn.status_id not in settled_status_ids():
        return None
    if txn.settled_basis_id is None:
        raise AmountUnresolvable(
            f"Transaction {txn.id} is in a settled status and records no "
            "settlement, so there is nothing to say what moved. "
            "status_seam.apply_status_change refuses to put a row in this "
            "state -- it is the ONE door that writes status_id, and it writes "
            "the record in the same call -- so a row here was written around "
            "that door. There is deliberately no fall back to the row's plan: "
            "the plan is a forecast and this row's money has already moved, "
            "which is the substitution this step exists to remove."
        )
    if txn.settled_basis_id == ref_cache.settlement_basis_id(
        SettlementBasisEnum.PURCHASES,
    ):
        return purchases_total(txn.entries)
    if txn.settled_amount is None:
        raise AmountUnresolvable(
            f"Transaction {txn.id} records a settlement whose basis stores its "
            "figure, and stores none. Only the 'purchases' basis leaves "
            "settled_amount NULL -- there the row's own entries state the "
            "figure -- so this row was written around that rule. There is "
            "deliberately no fall back to the row's plan: the plan is a "
            "derivation and can have moved since the money did, which is the "
            "substitution this step exists to remove."
        )
    return txn.settled_amount


def recorded_figure(row) -> "Decimal | None":
    """Return what *row* records as having moved, or ``None`` if it records none.

    **:func:`settled_figure`'s TOTAL twin, for the EDIT DOORS**, and the one
    clause between them is the whole difference: a settled row that records
    NOTHING answers ``None`` here and RAISES there.

    **The two answer different questions, which is why this is not a softened
    copy.**  :func:`settled_figure` is asked by everything that COUNTS money,
    and for those callers "nothing recorded" must be a refusal -- answering
    ``None`` would send them to the row's PLAN and publish a forecast as a fact,
    which is the substitution plan step X-au-c3 exists to remove.  This is asked
    by the two full-edit popovers, which do not count anything: they PREFILL a
    box, and for a row that records nothing the true prefill is an empty box.

    **The row it exists for is the one that most needs repairing.**  A settled
    row carrying no settlement record predates the record entirely (finding
    **N-181**); ``status_seam.apply_status_change`` refuses to create one and
    the X-au-c3 migration backfilled every instance, so production holds zero
    (measured on the 2026-08-17 clone: 166 settled rows, 0 without a basis).
    But such a row cannot be repaired from a surface that refuses to draw, and
    it cannot be repaired by stating its DAY alone either --
    ``ck_transactions_settle_day_needs_a_record`` pairs the two, so a day written
    without a record violates it.  The repair is to state BOTH, which is what
    the Actual box beside the day box is for.

    An INCOHERENT record -- a basis that stores its figure, storing none --
    still raises, deliberately.  That is not "nothing recorded", it is a record
    contradicting itself, and no door can produce one:
    ``status_seam.Settlement.__post_init__`` refuses to construct it and the
    seam writes both columns from that one value.

    Args:
        row: The row being asked.

    Returns:
        The recorded figure; ``None`` when the row has not settled or records
        nothing at all.

    Raises:
        AmountUnresolvable: From :func:`settled_figure`, when the row's record
            contradicts itself.
    """
    if row.settled_basis_id is None:
        return None
    return settled_figure(row)


def recorded_amounts_by_id(rows) -> "dict[int, Decimal | None]":
    """Return ``{transaction_id: what the row records}``, total where it records none.

    The batch the EDIT surfaces read, and :func:`settled_amounts_by_id`'s twin
    in exactly the way :func:`recorded_figure` is :func:`settled_figure`'s --
    see that function for why the two questions are different rather than one
    question with a lenient mode.

    Args:
        rows: The rows a full-edit form is about to render.

    Returns:
        ``{transaction_id: Decimal | None}`` covering every row.

    Raises:
        AmountUnresolvable: From :func:`recorded_figure`, for a row whose
            record contradicts itself.
    """
    return {row.id: recorded_figure(row) for row in rows}


def settled_amounts_by_id(rows) -> "dict[int, Decimal | None]":
    """Return ``{transaction_id: what the row RECORDED as having moved}``.

    The batch every SCREEN reads to show a settled row's figure (plan step
    X-au-c3), and the sibling of :func:`~app.services.cash_ledger.amounts_by_id`
    rather than a second spelling of it: that map answers what a row's amount
    IS -- its plan -- and this one answers what its money DID.  A screen shows
    the second where there is one and the first otherwise, which is the same
    precedence the balance uses (:func:`fixed_contribution`).

    **It lives HERE rather than beside its sibling in
    ``cash_ledger._amounts``, and the reason is the split this module IS**: the
    budget map takes an ``AmountBasis`` because a plan may be DERIVED and need a
    producer to answer it, while a settlement RECORD is the row's own and needs
    none.  The producer-free half belongs in the producer-free module -- and
    ``cash_ledger`` re-exports it exactly as it re-exports
    :func:`owned_contribution`, so no consumer names two modules.  It was
    written into ``_amounts`` and moved here in the same step, when that
    module's 1,000-line cap refused it: the cap did its job.

    ``None`` for a row that has not settled, which is what a template branches
    on.  It is published as a MAP rather than read off the row because the answer
    needs ``ref_cache`` to tell a ``purchases`` record from one that stores its
    figure, and a Jinja template comparing a ref id would be exactly the
    string-versus-id defect the project-wide ref rule forbids.

    Args:
        rows: The rows a surface is about to render.

    Returns:
        ``{transaction_id: Decimal | None}`` covering every row.

    Raises:
        AmountUnresolvable: From :func:`settled_figure`, for a row whose
            settlement record is incomplete.
    """
    return {row.id: settled_figure(row) for row in rows}


def fixed_contribution(txn) -> "Decimal | None":
    """Return what *txn* is worth WITHOUT resolving its amount, or ``None``.

    The one statement of the two arms that answer before the amount model is
    consulted at all, so every valuation built on it -- the batch, the one-row
    form, and the cheap accessor -- cannot come to disagree about them:

      * a row that does not contribute -- soft-deleted, Credit or Cancelled --
        is worth ``0``, whatever prices it; and
      * a row whose money has MOVED is worth what it recorded
        (:func:`settled_figure`), because a record of what left the account is a
        fact and a plan is a forecast.

    ``None`` means neither applies -- the row has not settled -- and its own
    amount decides, which is the resolver's question.

    **The second arm was ``txn.actual_amount`` until plan step X-au-c3**, and the
    difference is the whole step.  That column was only populated when a human
    typed a correction, so a settled row that had no correction answered ``None``
    here and fell through to its PLAN -- and because a plan is a derivation, the
    plan then had to be frozen at settle so a later price change could not move a
    recorded past.  A settled row now always answers, so no settled row's balance
    reads its plan and there is nothing to freeze.

    **The first arm is why the status gate sits ABOVE the resolver** (plan step
    X-au-c2).  ``Transaction.effective_amount`` answered ``$0.00`` for an
    excluded row from inside the valuation, where the resolver of the day would
    REFUSE the same row: both live producers filtered to Projected rows, so a
    Cancelled salary row was absent from the map and had no derived answer at
    all.  *NEITHER producer survives -- ``cash_ledger.LoanPricing.live_cash``
    went at plan step X-au-g-2c-2 and ``income_service.live_projected_net`` at
    **X-au-d** -- and the rules that replaced them read no status, so rule 2 and
    rule 4 now price a Cancelled row like any other.*  The ordering is kept
    anyway and is not merely vestigial: asking what a row is WORTH before
    asking what it is PRICED at is what stops an excluded row paying for a
    derivation nobody is counting, and it is what makes the gate a property of
    this module rather than a precondition each producer must restate.

    **Order matters between the two arms, and it is unchanged**: an excluded row
    is worth ``0`` even if it settled first and was cancelled after, because
    ``excludes_from_balance`` is a statement about whether the row counts at all.

    Args:
        txn: The row being valued.  ``is_deleted`` and the ``status``
            relationship are read (``status`` is ``lazy="joined"``), then the
            settlement record.

    Returns:
        The row's worth when it needs no resolution, else ``None``.

    Raises:
        AmountUnresolvable: Propagated from :func:`settled_figure` for a row
            whose settlement record is incomplete.
    """
    if not is_balance_contributing(txn):
        return Decimal("0")
    return settled_figure(txn)


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
    X-au-c2b): the cheap accessor for a reader that takes a row's own PLAN and
    can only ever see rows whose plan is their own.  Such a reader needs no
    amount basis, so it does not run the paycheck engine to re-derive a figure
    the row already states.

    **It answers the PLAN, never the settlement**, which is the whole distinction
    from :func:`owned_contribution` beside it.  Ruling E-21: a row's budget base
    is ``estimated_amount`` unconditionally, so a variance's two terms -- what
    was planned, and what moved -- stay two different reads.  Answering the
    settled figure here would make every settled row's variance zero by
    construction, which is the defect the spending report's surprises list
    exists to surface.

    **A SETTLED row does not automatically satisfy its precondition, and plan
    step X-au-c3 is where that stopped being true.**  This function's own
    docstring used to say that after the freeze every settled row owned its plan,
    because the freeze wrote the resolved figure into ``estimated_amount``.
    Nothing writes a plan column at settle now, so a settled row whose plan a
    later cutover declares DERIVED stores none and this refuses -- correctly, and
    loudly.  The readers that want a settled row's BUDGET (the spending report's
    variance) therefore owe an amount basis from the cutover that declares their
    rows onward; that obligation is finding **N-296**'s, owned by the first
    cutover to make it live.

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

    The cheap accessor for a reader that can only ever see SETTLED rows -- the
    loan split, the loan posting sync and reconcile, the settled-spend metric and
    the spending report.  Those readers filter to settled statuses in SQL, so
    building an amount basis for them would run the paycheck engine to re-derive
    a figure the row already recorded.

    **A settled row never reaches the second arm, and that is plan step
    X-au-c3's doing rather than a caller convention.**  :func:`fixed_contribution`
    answers every settled row from its SETTLEMENT, so the ``owned_amount``
    fall-through below is reached only by a row that has not settled --
    i.e. by a reader that has been handed a row outside its stated scope.  Before
    that step a settled row with no correction fell through to its plan, which is
    why this function had to be about ownership at all.

    **The name is the assertion, and the refusal is what makes it one.**  A row
    whose PLAN is derived stores no figure, and
    ``ck_transactions_amount_ownership`` is what pairs the two -- so this raises
    where ``effective_amount`` used to, rather than answering ``None`` into a
    money path.  A settled-only reader handed an unsettled derived row fails
    LOUDLY here instead of publishing a wrong number, which is what makes the
    per-kind cutovers (X-au-d..X-au-i) safe to ship one at a time.

    **TWO readers were not settled-only, and plan step X-au-g-2c-1 routed the
    first of them.**  ``loan_payment_service.get_payment_history`` admits
    Projected shadows and priced them here, so a derived loan-side income
    shadow broke the feed the amortization engine replays -- which is why the
    rule-4 controls declared only the checking-side EXPENSE leg, and which
    finding **N-266**(a) recorded
    (first as an irreducible CYCLE, then, once plan step X-au-g-1 deleted the
    path it named, as ONE UNROUTED READER).  That reader takes
    :func:`~app.services.cash_ledger.contributions_by_id` now, so nothing left
    in ``app/`` asks this accessor about a row that might be derived.

    **The SECOND is GONE TOO, and this paragraph is corrected rather than
    deleted because it is a REASON a later step would otherwise cite.**  It
    read: ``balance_at._plan._planned_from_shadows`` values PROJECTED loan-side
    shadows at ``owned_contribution(shadow)`` where the live-cash map has no
    entry, so one caller still asks this accessor about a row that might be
    derived.  That was true when written and false from plan step
    **X-au-g-2c-1**, which routed that reader through
    ``cash_ledger.display_amounts_by_id`` (now ``amounts_by_id``: plan step
    X-au-d deleted the live half of that composition) -- the census at that
    step found TWO unrouted readers where the finding had named one, and moved
    both.  Plan step
    **X-au-g-2c-2** then declared every transfer shadow derived, so the state
    this paragraph warned about is not merely unvisited but unreachable: the
    accessor would refuse, and no caller reaches it.

    *Both drafts of this paragraph were wrong in opposite directions -- the
    first said "every caller is now settled-only" while a survivor stood, the
    second kept naming that survivor after it had moved. An undated claim quoted
    as a REASON decays invisibly, because nobody re-checks a premise; the
    re-census that corrected it is recorded at the foot of this docstring.*

    **RE-CENSUSED AT PLAN STEP X-au-d (2026-09-03), and the re-run is the
    discipline rather than the result.**  Widening the derived class widens what
    this accessor can be handed, so the census is re-run at every cutover that
    widens it -- X-au-g-2c-2 for every transfer shadow, X-au-d for every
    non-override salary row INCLUDING the settled ones.  An adversarial review
    of X-au-d found this paragraph edited without the census being re-run, which
    would have left the next widening reading a date that did not cover it.
    *The merge of X-au-d with X-au-g-2c-3b-2 then proved the point a second time
    and from the other direction: the two steps edited THIS paragraph
    concurrently, and the list below was stale in the branch that had just
    re-run the census -- because the seventh site MOVED under it rather than
    changing count.  A census is re-run on the MERGED tree or it is not re-run.*
    All seven live call sites are settled-only or guarded:
    ``cash_ledger.settled_cash_leg``, ``loan_ledger._events.loan_event_stream``
    (which was ``._split.split_one_payment`` until plan step X-au-g-2c-3b-2 moved
    the cash read onto the event that carries it, and which reads SETTLED income
    shadows either way),
    ``loan_posting_service._sync`` and ``._display``,
    ``savings_dashboard_service._metrics`` (settled statuses in SQL), and the
    spending report's ``_window`` and ``_breakdown``.  ``statement_match`` and
    ``_release`` catch :class:`~app.exceptions.AmountUnresolvable` explicitly.
    **``cash_ledger._cash_leg`` guards only on ``is_balance_contributing``,
    which does NOT exclude Projected**, and is safe today only because both of
    its callers pre-filter -- a contract its own docstring already admits is
    invisible.  Widening the derived class makes that the tripwire for the next
    caller, and it is named here rather than left to be found.

    **The refusal below is what makes routing them safe to defer**, and that is
    worth keeping rather than treating as an accident: a reader handed a
    derived row fails LOUDLY here instead of feeding a ``None`` into a money
    path, so the per-kind cutovers ship one at a time and each reader they would
    break announces itself.

    Args:
        txn: The row being valued, whose ``estimated_amount`` is its own.

    Returns:
        ``0`` for a row that contributes nothing, the figure the row RECORDED
        when its money has moved, else the row's stored ``estimated_amount``.

    Raises:
        AmountUnresolvable: When the row has not settled and its plan is
            derived, so it stores no figure either way.
    """
    fixed = fixed_contribution(txn)
    if fixed is not None:
        return fixed
    return owned_amount(txn)
