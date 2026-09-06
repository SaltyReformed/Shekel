"""
Shekel Budget App -- What ONE row is worth, without asking any producer.

The pure half of the cash valuation: the arms that answer from the row itself
-- a status that excludes it, a soft delete, the SETTLEMENT it recorded, and
the figure the row OWNS.  Nothing here consults the amount model's live
producers, which is the whole reason it is a module of its own.

**The settlement arm is what makes the two halves total between them** (plan
step X-au-c3).  A row is a PLAN until its money moves and a RECORD of what moved
once it has, and the two never share a column: :func:`settled_figure` answers
from the record, the amount model answers from the plan, and a row is in
exactly one of the two states.  *The plan half used to be named here too, and
NOTHING of it survives.  It was an ``owned_amount`` accessor reading the
``estimated_amount`` column, which plan step X-bu deleted as a second spelling
of what :func:`~app.services.cash_ledger.resolve_transaction_amount` answers --
the two parted on a row whose plan a cutover had declared DERIVED (finding
BAL-462).  Its leaf, ``own_figure``, outlived it by one step, because
:func:`settled_contribution` below -- then named ``owned_contribution`` --
spelled the same column read at its
fall-through; plan step X-bx deleted that spelling too and moved the leaf into
``cash_ledger._amount_source``, where its only two callers are, as the private
``_own_figure`` (finding BAL-465).  This module states what a row RECORDED and
never what it PLANS.*  Before that split a settled row's record was
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
    :func:`settled_contribution`, so no consumer names two modules.  It was
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


def settled_contribution(txn) -> Decimal:
    """Return what a row that has SETTLED contributes, refusing one that has not.

    The cheap accessor for a reader whose rows have ALL settled -- the settled
    cash leg, the loan replay, the loan posting sync and its confirmed history,
    the settled-spend metric and the spending report.  **SIX of the seven load
    their rows with ``status_id.in_(settled_status_ids())`` in SQL**, so
    building an amount basis for them would run the paycheck engine to
    re-derive a figure the row already recorded.  The seventh is
    :func:`~app.services.cash_ledger.settled_cash_leg`, which issues no query of
    its own; of ITS six callers, three restrict the row set -- the walk loads
    settled statuses in SQL, the posting writer reaches its settled target only
    under ``settled=True`` (all fourteen ``sync_transaction_postings`` call
    sites derive that flag from the row rather than asserting it), and
    ``statement_match._candidates._price`` branches on ``txn.status.is_settled``.

    **THE OTHER THREE DO NOT RESTRICT ANYTHING, AND THAT IS DELIBERATE.**
    ``statement_match``'s ``_accepted_view._accepted_row``, ``_release
    ._subject_removal`` and ``._container_removal`` admit a row of any status
    and CATCH this refusal, because they render the review page and a raise
    there would make the screen permanently unreachable for the account with no
    in-app repair (finding **N-302**).  For them the refusal is an ANSWER, not a
    failure, so a reader of this function must not read "every caller is
    settled-only" into it: what is true is that nowhere does the refusal reach a
    user as a 500.

    **It is :func:`~app.services.cash_ledger.contribution_of`'s PARTIAL twin,
    and the two share every line but the last** (plan step X-bx).  Both gate an
    excluded row to ``0`` and answer a settled row from its SETTLEMENT, through
    the one :func:`fixed_contribution` below.  Where a row has NOT settled, that
    one resolves the row's plan and this REFUSES -- so the two can never
    disagree about a figure, only about whether there is one to give.

    **The refusal is the step, and what it replaced was a SECOND SPELLING of
    the plan** (finding **BAL-465**).  This fell through to
    ``own_figure(txn.estimated_amount, ...)``: the plan column read by hand,
    where ``cash_ledger.resolve_transaction_amount`` is the one producer of a
    row's plan.  That is the defect ``CLAUDE.md`` rule 14 names, and the one
    plan step X-bu deleted from the BUDGET side (``owned_amount``, finding
    **BAL-462**); it was unexposed here only because :func:`fixed_contribution`
    answers a settled row first, which is a head start and not a guarantee.

    **Routing that fall-through to the RESOLVER was the other option, and it is
    the wrong answer for these readers** (developer, 2026-09-06).  Their
    question is what a row's money DID, and a row that has not settled has done
    nothing: ``settled_cash_leg`` means CONFIRMED cash effect, so pricing a
    projected row's forecast there would publish a plan as a fact.  That is the
    substitution plan step X-au-c3 exists to remove, and :func:`settled_figure`
    refuses to perform it for exactly the same reason.  Resolving would also
    have made today's LOUD failure silent: a derived projected row raises here
    now, and the amount model would have priced it.

    **THE SEVEN-CALLER CENSUS THIS DOCSTRING CARRIED IS DELETED, AND THAT IS THE
    POINT OF THE STEP.**  It listed every call site and had to be RE-RUN at each
    per-kind cutover that widened the derived class, because a wider derived
    class widened what this accessor could be handed and the fall-through would
    price it from a column that may not exist.  There is nothing left to widen
    into: the precondition is stated by the call rather than asserted about it.

    *A mutation probe measured the change before it was made (2026-09-06): with
    this fall-through spliced to a refusal the full suite ran **13,338 passed,
    9 failed**, and all nine were TESTS calling the accessor directly on a
    Projected row, with no app frame in any traceback.  **That number bounds
    LESS than it appears to, and two adversarial reviews said so
    independently.**  Three ``statement_match`` readers catch
    :class:`~app.exceptions.AmountUnresolvable` by name and answer around it, so
    on those paths the refusal changes an ANSWER rather than failing a test --
    a green suite cannot tell "never reached" from "reached and re-answered"
    there.  What the probe does establish is the narrower claim: no path turns
    this into a 500.  The three catchers are graded by cases added with this
    step, because before it the suite entered none of them.*

    **The NAME changed with the body** -- it was ``owned_contribution``, and it
    asserted that the row owns its figure.  That is not what this function is
    about and was never what its readers meant, and the stale assertion was
    load-bearing rather than cosmetic: a :class:`~app.models.transfer.Transfer`
    was handed to it (``tests/test_routes/test_transfers.py``), and a
    ``Transfer`` defines no ``estimated_amount`` at all, so the Cancelled row's
    ``0`` was the only thing between that call and an ``AttributeError``.

    **The history the deleted census recorded is kept in ONE sentence, because
    it is a reason and not a list.**  Two readers were once not settled-only --
    ``loan_payment_service.get_payment_history`` (finding **N-266**(a)) and
    ``balance_at._plan._planned_from_shadows`` -- and plan step X-au-g-2c-1
    routed both onto :func:`~app.services.cash_ledger.contributions_by_id`,
    which is where a reader that may see a PROJECTED row still belongs.  The
    two drafts of that paragraph were wrong in opposite directions, one claiming
    every caller was settled-only while a survivor stood and the next still
    naming the survivor after it had moved; a claim quoted as a REASON decays
    invisibly, because nobody re-checks a premise.  Neither draft could be wrong
    now: the refusal states the precondition instead of a docstring asserting it.

    Args:
        txn: The row being valued.  Its caller's query has already restricted it
            to a settled status; ``is_deleted`` and the ``status`` relationship
            are read first (``status`` is ``lazy="joined"``), then the
            settlement record.

    Returns:
        ``0`` for a row that contributes nothing -- soft-deleted, Credit or
        Cancelled -- else the figure the row RECORDED as having moved.

    Raises:
        AmountUnresolvable: When the row has NOT settled, so it recorded
            nothing and this reader was handed a row outside its own scope;
            and from :func:`settled_figure`, when a settled row's record is
            incomplete.
    """
    fixed = fixed_contribution(txn)
    if fixed is None:
        raise AmountUnresolvable(
            f"Transaction {txn.id} has not settled, so it recorded nothing as "
            "having moved. This accessor answers what a row's money DID. "
            "There is deliberately no fall back to the row's PLAN, in either "
            "spelling: a plan is a forecast, and answering one here would "
            "report money as having moved when it has not. A caller that means "
            "the plan asks cash_ledger.contribution_of(txn, basis), which "
            "resolves it. Three statement_match readers CATCH this on purpose "
            "and answer around it (finding N-302); reaching it anywhere else "
            "means a settled-only reader was handed a row outside its scope."
        )
    return fixed
