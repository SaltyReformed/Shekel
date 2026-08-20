"""
Shekel Budget App -- Cash ledger: WHERE one row's amount comes from.

The TOTAL dispatch behind ruling **R-FI** (plan step **X-au-b**): a row's amount
is either its OWN -- a human authored the figure, or the money already moved --
or it is DERIVED, and a derived amount is not stored at all.  Five rules, one
per source, each delegating to the producer that already answers it.  Given a
row, :func:`resolve_transaction_amount` answers what its amount column holds or
would hold, and REFUSES rather than falling back for a row it cannot place.

**This is the sibling of :mod:`._amounts`, not a second copy of it, and the two
answer different questions.**  This module answers *what is this row's amount* --
the quantity ``budget.transactions.estimated_amount`` and
``budget.transfers.amount`` carry, and the one plan step X-au-c1 made NULLABLE.
:mod:`._amounts` answers *what is this row worth to checking*, which composes
that amount with an entered actual, an excluded status and an envelope's
purchases.  Splitting them by question is what the package already does
(:mod:`app.services.cash_ledger` docstring's table).  **It is a separate module
rather than more of ``_amounts``, and that was a developer decision**
(2026-08-12): the two questions are different tiers, and ``_amounts`` is 690
lines against a live ``too-many-lines`` ceiling of 1000 that nothing in ``app/``
exceeds.  A module INSIDE this package rather than a new top-level service,
because the W9909 completeness fence is keyed on the package and prefix-matched,
so a sibling module is scoped the day it is written -- where a new top-level
module would escape it, which is finding N-28's shape.

**The five rules, and the DECLARATION that picks between them.**  They are NOT a
partition over ``template_id`` / ``transfer_id`` -- ruling R-FI refuted that
discriminator by tracing -- because two of them are SUBSETS of two others:

  1. **OWN** -- the row states its own figure, and SAYS SO by carrying
     ``amount_source_id IS NULL``.  An ad-hoc row, a CC payback, and a row a
     human re-priced.  **A settled row is NOT automatically one of them**: plan
     step X-au-c3 records what MOVED in its own columns and writes no plan
     column at all, so a settled row's plan keeps whatever ownership it had.
  2. **SALARY** -- a paycheck, priced by the salary profile driving its
     template (``income_service.live_projected_net``).  A SUBSET of rule 3:
     ``SalaryProfile.template_id`` names an ordinary transaction template.
  3. **TEMPLATE** -- an ordinary recurring row, priced by its definition's
     effective-dated series as of the row's OWN due date
     (``template_amount_service.amount_as_of``, plan step X-au-a).
  4. **LOAN_PAYMENT** -- a loan payment's shadow, priced by the loan
     (``loan_payment_service.LoanPricing``).  A SUBSET of rule 5:
     a loan payment IS a transfer.
  5. **TRANSFER** -- any other transfer shadow, priced by its parent transfer,
     which is itself priced by rule 1 or rule 3
     (:func:`resolve_transfer_amount`).

**Ownership is DECLARED and the refinement is READ, and the split between the
two is the design** (plan step X-au-c2, finding **N-262**).  Plan step X-au-c1
added ``amount_source_id`` to both tables: NULL when the row owns its figure, and
otherwise the RELATION that prices it -- its recurring definition, or its parent
transfer.  :func:`amount_rule` asks that COLUMN which of the two states a row is
in, and asks the DEFINITION only for the refinement WITHIN a derived state:
whether a template is salary-linked, whether a transfer template carries
loan-payment settings.  The refinement stays a live read because a definition can
change mode -- ``routes/loan/payment_transfer.track_payment`` flips a payment to
derive-mode in one click, and archiving a salary profile unlinks a template -- so
a stored RULE would name a producer that no longer answers (ruling **R-FK**).

**What the column read buys is a resolver whose answer cannot contradict the
CHECK.**  Until plan step X-au-c2 the OWN arm was INFERRED from ``is_override``
and from having left Projected, neither of which
``ck_transactions_amount_ownership`` can see -- so four live doors could write a
row the schema admits and this dispatch refuses: a pay-period move alone sets the
flag (``routes/transactions/mutations.py:251``), carry-forward sets it in a bulk
``query.update`` no ORM validator sees (``carry_forward_service/_execute.py:157``),
and Credit and Cancelled leave Projected WITHOUT entering the settled band, so no
freeze ever fires.  Production carries 7 Cancelled and 2 Credit template-linked
rows and ``routes/grid/page.py``'s ``_load_grid_transactions`` loads every one
of them with no status predicate,
so the first bucket to derive would have taken out the whole screen.  Asking the
column instead makes the two agree by construction: the state the CHECK pairs a
figure with is exactly the state this dispatch answers from that figure.

X-au-c1 backfilled no declaration at all, so EVERY row on production is OWN and
this resolver answers its stored column through ONE arm.  That is what makes
X-au-c2's fifteen-module reader refactor byte-identical by construction rather
than by measurement -- before it, a Projected template-linked row priced from the
SERIES and agreed with its column only because X-au-b measured ``$0.00`` drift.
The per-kind cutovers (X-au-d..X-au-i) are what stamp a relation as each bucket
stops being priced.  A CC payback is the kind carrying NEITHER link while its
amount is derived (``credit_workflow.create_cc_payback_transaction`` copies the
source row's figure, ``entry_credit_workflow.sync_entry_payback`` re-states it as
the sum of the source's credit entries), so it places as OWN here and needs a
relation of its own to stop -- finding **N-243**, plan step X-au-i.

**A refusal is a refusal, never a fallback.**  Where a derived rule's producer
cannot answer -- no due date to resolve a series on, an EMPTY series, no live
net for the row's period, a loan whose basis will not resolve, a missing parent
-- :class:`~app.exceptions.AmountUnresolvable` is raised naming the row and the
rule.  Falling back to the stored column would publish exactly the stale figure
this arc exists to delete, and once a per-kind cutover (plan steps
X-au-d..X-au-i) declares that row's relation its column is NULL, so the fallback
would be a ``None`` in a money path.  Zero rows on
production take any refusal arm (measured 2026-08-12 over all 997), so each one
carries a seeded control instead.

**The DERIVATION tier is separate, and finding N-228 is why.**  The salary
derivation runs the paycheck engine over the owner's WHOLE pay-period set, so
asking it per row is quadratic work and was already measured as a defect.
:func:`amount_basis` holds each live derivation ONCE and hands the resolver
both; the rules read the derivation their own kind owns, so which rule applies
is never decided by which map a row appears in.  That distinction is the refuted
discriminator one level down.  It is keyed on a ``(user_id, scenario_id)`` pair
rather than on an ``Account`` -- it only ever read ``account.user_id`` -- so a
CROSS-ACCOUNT reader builds one basis for everything it loaded instead of
grouping its rows by account first (plan step X-au-c2).

**A basis is pinned to an OWNER and a SCENARIO, not to a row set** (plan step
X-au-c2b), and that is what makes "one pricing pass per read pass" structural
rather than a convention each surface remembers.  Everything expensive here is
scoped that way already -- the paycheck engine by owner, a loan's P&I and escrow
history by loan -- so storing per-row ANSWERS made a pass row-set-shaped for no
reason and cost a re-derivation every time a request loaded a second row set
(findings **N-268**, **N-269**).  A read pass carries its own through
``balance_at.BalanceContext.amounts``; a write door that prices one row builds
one and pays only for the rules that row reaches.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain data and ORM
rows in, ``Decimal`` out; no Flask import, no writes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.enums import AmountSourceEnum
from app.exceptions import AmountUnresolvable
from app.services import template_amount_service
from app.services.row_valuation import own_figure, owned_amount
from app.utils.entry_partition import partition_entries
from app.utils.money import round_money

from ._amount_basis import AmountBasis
from ._amount_rule import AmountRule, amount_rule, declared_relation


def resolve_transaction_amount(txn, basis: AmountBasis) -> Decimal:
    """Return what *txn*'s amount is, by the rule that owns it.

    The resolver plan step X-au-b exists to build.  It is TOTAL over rows -- one
    of five rules applies to every one -- and total in its answer: it returns a
    ``Decimal`` or it raises, never ``None`` and never a plausible substitute.

    Proven equal to the answer the app gives today for **every one of the 997
    rows** on the 2026-08-12 production clone
    (``tests/manual/verify_amount_resolver.py``).  "The answer the app gives
    today" is not single-valued -- ``routes/grid/page.py``'s ``index`` reads the override map
    while ``dashboard_service.py:279`` reads the raw column, which IS finding
    **N-224** -- so the oracle grades the OVERRIDE-MAP answer, the one every
    balance surface folds.

    **It is UNWIRED as of this step.**  Nothing in ``app/`` calls it yet; plan
    step X-au-c2 routes the readers through it, and X-au-c3 makes a settled row
    RECORD what moved so no settled row is priced here at all.

    **A caller resolving many rows should eager-load SEVEN relationships**, and
    an adversarial review counted them after a first draft named two: per row,
    ``Transaction.template`` and ``Transaction.transfer``; per template,
    ``TransactionTemplate.salary_profiles`` and ``.amount_versions``; per
    transfer, ``Transfer.template``, and per transfer template ``.settings`` and
    ``.amount_versions``.  Every one is ``lazy="select"``.  The per-TEMPLATE
    ones cost one query per distinct definition rather than per row (the
    collections are identity-mapped, so 44 templates serve 452 rows on the
    production clone); the per-ROW ones are a true N+1.  Stated rather than
    hidden: the eager load belongs in the loaders plan step X-au-c2 routes, not
    in a per-row rule.

    **It no longer asks whether *txn* was in the basis's row set** (plan step
    X-au-c2b).  A basis holds DERIVATIONS pinned to an owner and a scenario, not
    answers pinned to a row set, so every row of that owner and scenario is
    resolvable against it and the membership refusal this used to raise has no
    state left to describe.

    **What it asks INSTEAD is the pin the caller can still get wrong**, and the
    substitution is the point rather than a smaller guard.  ``priced_ids``
    caught "this row was not in the set I built over"; it could not catch "this
    basis belongs to another scenario", which is the mistake that actually
    changes a figure -- ``LoanPricing`` resolves a loan against ITS scenario's
    payment history, so a foreign basis answers a different ``monthly_payment``
    with no error at all.  A row states its own ``scenario_id`` in a column, so
    the check is free and TOTAL where the membership set was neither.

    An adversarial review of this step's own build is what put it here: the
    control that replaced the deleted membership test proved the SAFE direction
    (a basis answers for a row it was not built over) and left the unsafe one --
    a silently different number -- with nothing asserting it.

    Args:
        txn: The :class:`~app.models.transaction.Transaction` to price.  It must
            belong to *basis*'s scenario.
        basis: The read pass's :class:`AmountBasis` (:func:`amount_basis`).

    Returns:
        The row's amount as a ``Decimal``.

    Raises:
        AmountUnresolvable: When *txn* belongs to another scenario than *basis*,
            or when the rule that owns this row cannot answer for it.  See the
            module docstring: a refusal is never a fallback.
        UndatedSettleError: Propagated from the DERIVE-mode loan arm, whose
            producer loads the loan's payment history and refuses a settled
            payment carrying no settle day
            (``balance_predicates.settled_day``).  Named here because a caller
            catching only :class:`~app.exceptions.AmountUnresolvable` would
            otherwise meet it unannounced.
    """
    if txn.scenario_id != basis.scenario_id:
        raise AmountUnresolvable(
            f"Transaction {txn.id} is in scenario {txn.scenario_id} and this "
            f"AmountBasis prices scenario {basis.scenario_id}. Resolving it "
            "here would price it from another scenario's salary profiles and "
            "another scenario's loans -- a different figure, with nothing to "
            "say so. Build the basis for the scenario you are pricing."
        )
    return _RULE_ANSWERS[amount_rule(txn)](txn, basis)


def amounts_by_id(rows, basis: AmountBasis) -> dict[int, Decimal]:
    """Return ``{transaction_id: what the row's amount IS}`` for *rows*.

    **The batch every reader that takes a row's BUDGET uses** (plan step
    X-au-c2b), and the sibling of
    :func:`~._amounts.contributions_by_id` rather than a second spelling of it.
    The two answer different questions and a reader wants one or the other:

      * a CONTRIBUTION is what the row is worth to a balance -- ``0`` for an
        excluded row, what the row RECORDED as having moved once it has settled
        (``row_valuation.settled_figure``), else the resolved amount;
      * an AMOUNT is what the row's budget IS, unconditionally.

    **Ruling E-21 is why the second exists.**  An entry-tracked bill row's
    budget base is ``estimated_amount`` -- never what the row recorded as
    having moved, never status-dependent -- so the row's three figures (the amount cell, the
    remaining, the over-budget flag) all answer one question.  A contribution
    would break both halves of that: it answers ``$0.00`` for a Cancelled
    envelope, whose budget is still its budget, and it answers the entered
    actual for a settled one, which would silently re-base a variance on the
    number it is being compared against.

    **There is deliberately NO status gate above the resolve**, which is the one
    place this differs in shape from ``contributions_by_id``.  That batch gates
    first because an excluded row is worth ``$0.00`` and has no producer to
    answer it; here the excluded row's budget is exactly what is being asked
    for, so the resolver is asked for every row.  A per-kind cutover
    (X-au-d..X-au-i) that declares a row derived owes that row a producer that
    answers whatever its status -- and rule 2 and rule 4 stopped reading status
    at this same step precisely so they can.

    Every id in *rows* appears in the result, so a caller indexes it with ``[]``
    and a row it forgot to price raises a ``KeyError`` where it is read.  There
    is deliberately no ``.get(id, default)`` shape: a default here is a
    fabricated figure in a money path.

    Args:
        rows: The loaded rows to price.  They may span accounts -- the basis is
            keyed on the owner, not on one account -- but every one must belong
            to *basis*'s owner and scenario.
        basis: The read pass's :class:`AmountBasis`.

    Returns:
        ``{transaction_id: Decimal}`` covering every row.

    Raises:
        AmountUnresolvable: From the resolver, for a row whose rule cannot
            answer.  A refusal is never a fallback (see the module docstring).
    """
    return {row.id: resolve_transaction_amount(row, basis) for row in rows}


def resolve_transfer_amount(xfer) -> Decimal:
    """Return what a parent TRANSFER's amount is.

    ``budget.transfers.amount`` is the second column ruling R-FI's CHECK covers,
    so its rules belong here beside the transaction's rather than in a module of
    their own.  Only two of the five can apply: a transfer owns its figure, or
    its definition's series states it as of the transfer's own due date.  Which
    of the two is the same question :func:`amount_rule` asks one table over --
    the ``amount_source_id`` column, not an inference from ``is_override``, from
    having left Projected, or from carrying a template (plan step X-au-c2,
    finding **N-262**).  An AD-HOC transfer is structurally in the first state:
    ``ck_transfers_adhoc_owns_amount`` refuses a declaration on one, because
    nobody generated it and no definition states its price.

    It takes no :class:`AmountBasis`, and that is a fact about the loan rule
    rather than an omission: ``LoanPricing.derive_cash`` resolves the escrow on
    the SHADOW's own due date, so a derive-mode loan payment has no
    transfer-level answer to give.  Such a transfer therefore reaches the series
    arm and is REFUSED by
    its OWNERSHIP test -- ``template_amount_service.owns_its_amount`` is False
    for a derive-mode payment, and that is the arm that fires rather than the
    empty-series one, because the template may still hold versions stated while
    it was manual.  The refusal is correct today and is what plan step X-au-f
    answers.

    Args:
        xfer: The :class:`~app.models.transfer.Transfer` to price.

    Returns:
        The transfer's amount as a ``Decimal``.

    Raises:
        AmountUnresolvable: When the transfer declares a relation that cannot
            price a transfer, or when it is priced by its definition and that
            definition states no price for its due date.
    """
    if xfer.amount_source_id is None:
        return own_figure(xfer.amount, "transfer", xfer.id)
    relation = declared_relation(xfer.amount_source_id)
    if relation is not AmountSourceEnum.TEMPLATE:
        raise AmountUnresolvable(
            f"Transfer {xfer.id} declares amount source {relation.value!r}, "
            "and a transfer has no parent transfer for one to name. Only a "
            "transfer TEMPLATE can price a transfer; a shadow transaction is "
            "the row that names its parent. This row was stamped by a writer "
            "that confused the two tables."
        )
    return _stated_amount(
        xfer.template, xfer.due_date, "transfer", xfer.id,
    )


def _stated_amount(template, on_date: date | None, kind: str, row_id: int) -> Decimal:
    """Return what *template* states for ``on_date``, refusing when it states nothing.

    The series arm shared by rule 3 and by :func:`resolve_transfer_amount`, so
    the two cannot come to disagree about what "the definition's price" means.
    Resolution itself is ``template_amount_service.amount_as_of``: the newest
    version at or before the date, holding FLAT before the earliest one, which
    is what makes it total for a row generated into a historical period.

    Three refusals, and all three are states the app can reach:

    * **the definition does not own its amount**
      (``template_amount_service.owns_its_amount`` is False -- a salary-linked
      template, a derive-mode loan payment).  Its price is COMPUTED by something
      else, so any versions it holds are dormant rather than authoritative.
      **The test for this is not "is the series empty", and a control found the
      difference**: a template switched from manual to derive-mode KEEPS the
      versions stated while it was manual (X-au-a's stated behaviour -- they are
      the record of what was stated then), so an emptiness test would answer a
      derive-mode payment from a price nobody is stating any more.
    * **no due date.**  ``due_date`` is nullable on both row tables and the
      transfer edit form can clear it, so a row can carry no date to resolve
      on.  A pay period's bounds are NOT a substitute -- a period begins up to
      two weeks before the installment it funds (ruling D5's contract time), so
      a price change inside that window would answer one figure here and
      another everywhere else.
    * **an empty series** on a definition that DOES own its amount: its creator
      wrote the scalar without going through
      ``template_amount_service.set_amount``, the one write door.  Nobody ever
      stated a price, and X-au-a's own docstring names this refusal as the
      reason it answers ``None`` instead of guessing.

    Args:
        template: The transaction or transfer template that states the price.
        on_date: The row's own due date.
        kind: ``"transaction"`` or ``"transfer"``, for the refusal message.
        row_id: The row's id, named in the refusal.

    Returns:
        The stated amount on ``on_date``.

    Raises:
        AmountUnresolvable: When the definition's amount is derived rather than
            stated, when the row has no due date, or when the series is empty.
    """
    if template is None:
        raise AmountUnresolvable(
            f"{kind.capitalize()} {row_id} names a template that could not be "
            "loaded, so the definition that states its price is gone. The FK "
            "is ON DELETE SET NULL, so the database cannot hold this pairing -- "
            "it is a row whose template was hard-deleted in this same session "
            "while the row still carried the id."
        )
    if not template_amount_service.owns_its_amount(template):
        raise AmountUnresolvable(
            f"{kind.capitalize()} {row_id} is priced by template "
            f"{template.id}, whose own amount is DERIVED rather than stated -- "
            "a salary-linked template, or a loan payment in derive mode. Its "
            "price series is dormant and may still hold versions stated while "
            "it owned its amount, so reading it here would answer a price "
            "nobody is stating any more. The rule that prices this row is the "
            "one that computes the definition's amount, and it had no answer."
        )
    if on_date is None:
        raise AmountUnresolvable(
            f"{kind.capitalize()} {row_id} is priced by template "
            f"{template.id} and carries no due_date, so there is no date to "
            "resolve its price on. Its pay period's bounds are not a "
            "substitute: a period starts up to two weeks before the "
            "installment it funds, so a price change inside that window would "
            "answer differently here than everywhere else."
        )
    stated = template_amount_service.amount_as_of(template, on_date)
    if stated is None:
        raise AmountUnresolvable(
            f"{kind.capitalize()} {row_id} is priced by template "
            f"{template.id}, which states no amount for {on_date.isoformat()} "
            "-- its price series is EMPTY. Either its amount is derived rather "
            "than stated (template_amount_service.owns_its_amount is False), "
            "or it was created without going through set_amount, the one write "
            "door. There is deliberately no fallback to default_amount: that "
            "scalar has no time dimension, so reading it here would price a "
            "March row at June's figure."
        )
    return stated


def _own_answer(txn, _basis: AmountBasis) -> Decimal:
    """Rule 1: the row states its own figure.

    Takes the basis it does not read, because every rule answers through ONE
    signature -- which is what lets the dispatch below be a mapping keyed on the
    rule rather than five special cases.

    Args:
        txn: The transaction being priced.

    Returns:
        The row's stored ``estimated_amount``.
    """
    return owned_amount(txn)


def _salary_answer(txn, basis: AmountBasis) -> Decimal:
    """Rule 2: a paycheck is worth what its salary profile pays for that period.

    Delegates to the map ``income_service.live_projected_net`` built, which is
    the same figure the salary projection page renders and the same one the
    recurrence engine writes at generation (DH-#30: both resolve tax configs per
    period YEAR).

    **The refusal fires exactly where the app holds two answers**, which is why
    it is a refusal.  ``live_projected_net`` scopes its profile lookup by
    SCENARIO while generation's ``_get_salary_profile`` takes the first active
    profile whatever its scenario, so a template driven by profiles in two
    scenarios is priced by one profile at write time and by another -- or by
    none -- at read time.  A row this rule cannot place is one of those, or one
    whose pay period the profile's projection does not cover, or an EXPENSE row
    on a salary-linked template (``live_projected_net`` takes income only).
    Zero such rows on the 2026-08-12 production clone.

    **It reads no STATUS, and that is plan step X-au-c2b's split.**  The map it
    used to index was built by the read-time repair, which filters to Projected
    non-overridden rows -- so a Cancelled or hand-priced paycheck was refused
    here for a reason that has nothing to do with what a paycheck is worth.
    Pricing asks the definition; whether a row still counts is finding
    **N-262**'s separate question, answered above this rule by
    ``row_valuation.fixed_contribution`` and beside it by
    ``income_service.live_projected_net``.

    Args:
        txn: The salary income row being priced.
        basis: The read pass's basis; its ``salary`` derivation holds the
            answer, keyed on the row's template and pay period.

    Returns:
        The live net pay for the row's period.

    Raises:
        AmountUnresolvable: When the live recompute has no answer for this row.
    """
    # Pylint: ``import-outside-toplevel`` -- the paycheck / tax stack stays off
    # this module's load path, the same reason ``amount_basis`` imports it at
    # call time (finding N-267).
    # pylint: disable=import-outside-toplevel
    from app.services.income_service import salary_net_for
    net = salary_net_for(txn, basis.salary)
    if net is None:
        raise AmountUnresolvable(
            f"Transaction {txn.id} is priced by a salary profile and the live "
            "recompute answered nothing for it. Either no ACTIVE profile names "
            "its template in this row's scenario -- generation resolves the "
            "profile without scoping by scenario and the recompute scopes by "
            "it, so the two can disagree -- or the profile's projection does "
            "not cover this row's pay period, or the row is an expense on a "
            "salary template. The stored figure is not a fallback: it is the "
            "cache this rule exists to stop reading."
        )
    return net


def _template_answer(txn, _basis: AmountBasis) -> Decimal:
    """Rule 3: a recurring row is worth what its definition states on its due date.

    Takes the basis it does not read: the price series is a STORED fact
    resolved per row, not a live recompute that has to be batched.

    Args:
        txn: The template-generated row being priced.

    Returns:
        The definition's stated amount as of the row's due date.

    Raises:
        AmountUnresolvable: See :func:`_stated_amount`.
    """
    return _stated_amount(txn.template, txn.due_date, "transaction", txn.id)


def _loan_payment_answer(txn, basis: AmountBasis) -> Decimal:
    """Rule 4: a loan payment's shadow is worth what the loan says it costs.

    Two arms, one per MODE, and the mode is read off the settings row
    (``loan_payment_service.loan_payment_config``) rather than inferred from
    which map the row turned up in:

    * **derive mode** -- the cash is P&I plus the escrow in effect on the
      shadow's own DUE date plus any standing extra, which
      ``LoanPricing.derive_cash`` computes from the loan's own resolution.  A
      ``None`` there means the loan would not resolve, and that REFUSES;
    * **manual mode** -- the operator owns the base cash, which is a STATED
      amount, so it is the definition's series (rule 5's arm, reached through
      the parent) plus the standing extra.

    **The mode is read rather than inferred, and an adversarial review is why.**
    The first draft answered "the live map when it has an entry, else the
    parent's series", which made a manual payment resolve TWO different ways:
    ``LoanPricing.live_cash`` prices a manual payment from
    ``shadow.estimated_amount + extra`` (``_manual_shadow_amount``), so a
    payment with a standing extra was answered from the stored column while the
    same payment without one was answered from its series.  On a shadow whose
    cache had drifted from its definition the two disagreed -- ``$1,400.00``
    against ``$1,450.00`` on the review's reproduction -- and the arm that won
    was the one reading the column ruling R-FI exists to stop reading.  Now
    neither manual arm reads it.

    **The derive arm reads no STATUS, and that is plan step X-au-c2b's split.**
    The map it used to index was built by the read-time repair, which filters to
    Projected non-overridden shadows -- so a Cancelled or hand-priced loan
    payment was refused here as though its LOAN would not resolve, which is a
    different and alarming statement.  Whether a row still counts is finding
    **N-262**'s separate question, answered above this rule rather than inside
    it.

    **The derive arm still reads the wall clock, through the derivation it
    delegates to** -- :class:`~app.services.loan_payment_service.LoanPricing`
    pins ``date.today()`` when the basis is built,
    which is finding **N-40**, owned by plan step X-au-g: the
    leaf that rules a shadow's P&I onto its own due date as ruling D5 already
    put its escrow.  The README states clock-freedom as the amount model's
    precondition against the SALARY derivation, where plan step X-as closed it;
    this is the remaining read and it is disclosed rather than claimed absent.
    Dormant on production (``budget.loan_payment_settings`` is empty), so this
    rule prices ``$0.00`` there and is graded only on a seeded loan.

    Args:
        txn: The loan-payment shadow being priced.
        basis: The read pass's basis; its ``loans`` derivation resolves the
            destination loan and holds its escrow history.

    Returns:
        The shadow's live cash.

    Raises:
        AmountUnresolvable: When a DERIVE-mode payment's loan will not resolve,
            or when a MANUAL payment's definition states no price.
    """
    # Pylint: ``import-outside-toplevel`` -- the loan-resolver stack stays off
    # this module's load path, the same reason ``amount_basis`` imports it at
    # call time.
    # pylint: disable=import-outside-toplevel
    from app.services.loan_payment_service import loan_payment_config
    derive, extra = loan_payment_config(txn.transfer.template)
    if derive:
        live = basis.loans.derive_cash(
            txn, txn.transfer.to_account_id, extra,
        )
        if live is None:
            raise AmountUnresolvable(
                f"Transaction {txn.id} is a DERIVE-mode loan payment and the "
                "loan would not resolve, so its P&I has no answer. The "
                f"destination account {txn.transfer.to_account_id} carries no "
                "LoanParams, or its schedule could not be built. The stored "
                "figure is not a fallback: on a derive-mode payment it is a "
                "snapshot of exactly the computation that just failed."
            )
        return live
    return round_money(resolve_transfer_amount(txn.transfer) + extra)


def _transfer_answer(txn, _basis: AmountBasis) -> Decimal:
    """Rule 5: a shadow is worth exactly what its parent transfer is.

    **This is Transfer Invariant 3 made structural rather than maintained.**
    Today the parent's figure is COPIED onto both shadows by
    ``transfer_service.update_transfer`` and a drift corrector in
    ``restore_transfer`` logs and repairs the copies that got away; a shadow
    that reads its parent cannot drift from it at all.  Measured on the
    2026-08-12 production clone: all 298 projected shadows already equal their
    parent, so the rule moves nothing and removes the way it could.  Plan step
    X-au-f is where the copy and the corrector are deleted.

    Takes the basis it does not read: the parent's own rules -- own figure, or
    its definition's series -- need no live producer.

    Args:
        txn: The transfer shadow being priced.

    Returns:
        The parent transfer's resolved amount.

    Raises:
        AmountUnresolvable: When the shadow has no parent, or the parent's own
            rule cannot answer.
    """
    if txn.transfer is None:
        raise AmountUnresolvable(
            f"Transaction {txn.id} is a transfer shadow whose parent transfer "
            f"{txn.transfer_id} could not be loaded, so there is nothing for "
            "its amount to be equal to. Every transfer has exactly two shadows "
            "and a shadow is never orphaned (Transfer Invariants 1 and 2), so "
            "this row breaks one of them."
        )
    return resolve_transfer_amount(txn.transfer)


def _credit_source(txn):
    """Return the row *txn* repays, refusing when it cannot be loaded.

    Shared by both payback arms so the missing-source refusal is stated once.
    A payback's whole figure is a property of its source, so a payback that
    cannot reach one has no amount to derive rather than a smaller one --
    answering zero would take a real card balance out of a projection silently.

    Args:
        txn: The payback row, which has declared the ``credit_source`` relation.

    Returns:
        The source transaction it repays.

    Raises:
        AmountUnresolvable: When the link is unset or the row behind it could
            not be loaded.
    """
    source = txn.credit_payback_for
    if source is None:
        raise AmountUnresolvable(
            f"Transaction {txn.id} is a CC payback whose source row "
            f"{txn.credit_payback_for_id} could not be loaded, so there is no "
            "card spend for it to repay. A payback names its source through "
            "fk_transactions_credit_payback_for, which is NOT NULL for a row "
            "declaring the credit_source relation, so this row was written "
            "around that link."
        )
    return source


def _cc_payback_purchases_answer(txn, _basis: AmountBasis) -> Decimal:
    """Rule 6: a payback is worth the CREDIT PURCHASES of its source envelope.

    The source holds its money in individual purchases, and the ones marked
    ``is_credit`` are exactly the spend that went on the card -- which is what
    ``TransactionEntry.settled_on``'s own column comment already states: *"a
    credit purchase never touches checking (it flows through its CC Payback
    sibling)"*.  So the payback repays their sum, and this rule READS what
    ``entry_credit_workflow.sync_entry_payback`` used to WRITE on every entry
    mutation (finding **N-243**).

    **It reads the SOURCE's credit entries rather than the entries that name
    this payback**, and the difference is which fact is underived.  Each credit
    entry also carries ``credit_payback_id`` pointing back here, maintained by
    the same function that maintained the figure -- a second copy with a second
    maintainer, which is the shape ruling R-FI deletes.  The partition over
    ``is_credit`` is the fact; the back-link is a pointer to it.

    ``partition_entries`` is the project's one definition of the credit-vs-debit
    split (DH-#75), shared with ``entry_service.compute_entry_sums``, so this
    rule and the envelope's own progress bar cannot come to disagree about which
    purchases are on the card.

    Takes the basis it does not read: a purchase states its own amount, so no
    live derivation is reached.

    Args:
        txn: The payback being priced.  Its ``credit_payback_for`` and that
            row's ``entries`` are read.

    Returns:
        The sum of the source's credit purchases, ``0`` when it has none --
        which is a real answer rather than a refusal, because an envelope whose
        card purchases were all removed genuinely owes the card nothing.  The
        payback is DELETED in that state by
        ``entry_credit_workflow.sync_entry_payback``, so the zero is what a
        reader sees between the removal and the sync within one unit of work.

    Raises:
        AmountUnresolvable: When the source row could not be loaded.
    """
    _, credit_entries = partition_entries(_credit_source(txn).entries)
    return round_money(sum((e.amount for e in credit_entries), Decimal("0")))


def _cc_payback_row_answer(txn, basis: AmountBasis) -> Decimal:
    """Rule 7: a payback is worth the WHOLE source row that went on the card.

    The source is not entry-capable, so it is a single spend and marking it
    Credit put all of it on the card.  What that spend IS is the source's own
    amount question, so this delegates to the resolver rather than restating it
    -- the same shape as rule 5, where a shadow delegates to its parent transfer
    instead of holding a copy.  It is what
    ``credit_workflow.create_cc_payback_transaction`` used to COPY at the moment
    of the mark and repair never (finding **N-243**).

    **The delegation cannot cycle**, and it is stated rather than guarded: a
    payback names a source that already existed when the payback was created, so
    ``credit_payback_for_id`` runs strictly backwards in creation order and the
    links form a DAG.  A chain longer than one link needs a payback to itself be
    marked Credit, which the grid does offer (``data-can-credit`` is emitted for
    any Projected, non-transfer, non-envelope expense), so depth is bounded by
    how many times an owner does that and not by one.

    **The source's STATUS is deliberately not consulted.**  A marked source is
    ``Credit``, which ``row_valuation.fixed_contribution`` values at ``0``
    because a Credit row contributes nothing to a balance -- but what this rule
    needs is the source's BUDGET, not its contribution, and ``amounts_by_id``
    states the same distinction for the same reason.  Asking for the
    contribution here would price every row-backed payback at ``$0.00``.

    Args:
        txn: The payback being priced.
        basis: The read pass's basis, passed through to the source's own rule --
            the source may be template-priced, so it can need one.

    Returns:
        The source row's resolved amount.

    Raises:
        AmountUnresolvable: When the source could not be loaded, or when the
            source's own rule cannot answer for it.
    """
    return resolve_transaction_amount(_credit_source(txn), basis)


# The TOTAL dispatch: one answer per rule, keyed by the rule itself.  A mapping
# rather than a chain of ``if``s so that adding a member to :class:`AmountRule`
# without an answer for it raises at the lookup instead of falling through to
# whichever branch happens to be last -- which is how a five-rule dispatch
# silently becomes a four-rule one.  ``tests/test_services/test_amount_source.py``
# grades the table against the enum, so the completeness is a predicate rather
# than a comment.
_RULE_ANSWERS = {
    AmountRule.OWN: _own_answer,
    AmountRule.SALARY: _salary_answer,
    AmountRule.TEMPLATE: _template_answer,
    AmountRule.LOAN_PAYMENT: _loan_payment_answer,
    AmountRule.TRANSFER: _transfer_answer,
    AmountRule.CC_PAYBACK_PURCHASES: _cc_payback_purchases_answer,
    AmountRule.CC_PAYBACK_ROW: _cc_payback_row_answer,
}
