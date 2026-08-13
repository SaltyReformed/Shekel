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
     ``amount_source_id IS NULL``.  An ad-hoc row, a CC payback, a row a human
     re-priced, and every row whose settle FROZE its figure (plan step X-aq,
     which plan step X-au-c3 formalises).
  2. **SALARY** -- a paycheck, priced by the salary profile driving its
     template (``income_service.live_projected_net``).  A SUBSET of rule 3:
     ``SalaryProfile.template_id`` names an ordinary transaction template.
  3. **TEMPLATE** -- an ordinary recurring row, priced by its definition's
     effective-dated series as of the row's OWN due date
     (``template_amount_service.amount_as_of``, plan step X-au-a).
  4. **LOAN_PAYMENT** -- a loan payment's shadow, priced by the loan
     (``loan_payment_service.live_loan_transfer_amounts``).  A SUBSET of rule 5:
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
rows and ``routes/grid.py:226`` loads every one of them with no status predicate,
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

**The batch tier is separate, and finding N-228 is why.**
``income_service.live_projected_net`` runs the paycheck engine over the owner's
WHOLE pay-period set, so asking it per row is quadratic work and was already
measured as a defect.  :func:`amount_basis` calls each live producer ONCE for a
row set and hands the resolver two maps; the rules read the map their own kind
owns, so which rule applies is never decided by which map a row appears in.  That
distinction is the refuted discriminator one level down.  It is keyed on a
``(user_id, scenario_id)`` pair rather than on an ``Account`` -- it only ever read
``account.user_id`` -- so a CROSS-ACCOUNT reader builds one basis for everything
it loaded instead of grouping its rows by account first (plan step X-au-c2).

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain data and ORM
rows in, ``Decimal`` out; no Flask import, no writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from app import ref_cache
from app.enums import AmountSourceEnum
from app.exceptions import AmountUnresolvable
from app.services import template_amount_service
from app.services.row_valuation import own_figure
from app.utils.money import round_money


class AmountRule(Enum):
    """Which of ruling R-FI's five sources a row's amount comes from.

    The dispatch key.  An explicit enum rather than a pair of link tests, because
    the rules are not a partition over the links -- see this module's docstring
    for the two subset relations that make the refinement order load-bearing.

    **It is NOT what the ``amount_source_id`` column stores, and a first draft of
    this docstring said it was.**  That column names the RELATION that prices a
    row -- its definition, or its parent transfer
    (:class:`app.enums.AmountSourceEnum`) -- and the refinement between SALARY
    and TEMPLATE, or between LOAN_PAYMENT and TRANSFER, is a property of the
    DEFINITION rather than of the row, resolved live here.  Storing the rule
    would put a definition-level fact on every generated row, where two live
    routes falsify it (ruling **R-FK**, plan step X-au-c1).

    **The OWN member is the one that IS the column** (finding **N-262**, closed
    at plan step X-au-c2): a row owns its amount exactly when it carries no
    source, which is the same NULL-ness ``ck_transactions_amount_ownership``
    pairs with carrying a figure.  Before that leaf this member was inferred from
    ``is_override`` and from having left Projected -- states the CHECK cannot see
    -- so the schema and this dispatch could disagree about the same row.  The
    status gate now sits ABOVE the resolver rather than inside it: an excluded row
    is worth ``$0.00`` whatever prices it, and asking a Projected-only producer
    about a Cancelled row is how that used to become a refusal
    (:func:`app.services.cash_ledger.contributed_amount`).
    """

    OWN = "own"
    SALARY = "salary"
    TEMPLATE = "template"
    LOAN_PAYMENT = "loan_payment"
    TRANSFER = "transfer"


@dataclass(frozen=True)
class AmountBasis:
    """The live per-row answers, resolved ONCE for an account's rows.

    Built by :func:`amount_basis` and consumed by
    :func:`resolve_transaction_amount`.  The two maps stay APART rather than
    merged, and that is the whole reason this type exists: a merged map makes
    "which rule applies" a question about map membership, which is the
    link-derived discriminator ruling R-FI refuted, and it hides which producer
    answered.  ``_amounts.live_amount_overrides`` merges them for the callers
    that still want one map, and does it in one place.

    **It records the row set it was BUILT over, and an adversarial review is
    why.**  Without :attr:`priced_ids` a basis MISS is indistinguishable from a
    producer's deliberate omission, and the two want opposite answers: a manual
    loan payment is absent from ``loan_cash`` because there is nothing to
    re-derive, while a row the caller never passed to :func:`amount_basis` is
    absent because the caller made a mistake.  The review reproduced the
    consequence -- a manual payment resolved outside its own basis answered
    ``$1,250.00`` where the correct figure was ``$1,400.00``, silently dropping
    a standing ``$150.00`` extra with no refusal.  An argument a caller can get
    wrong is a defect rather than a contract (``docs/plans/lessons.md``), so the
    set is carried and :func:`resolve_transaction_amount` checks it.

    Attributes:
        priced_ids: The ids of every row this basis was built over.  A row
            outside it cannot be resolved against this basis.
        salary_net: ``{transaction_id: net pay}`` for salary-linked Projected
            income (:func:`app.services.income_service.live_projected_net`).
            Empty when the account holds no such row -- the common case.
        loan_cash: ``{transaction_id: cash}`` for the DERIVE-mode loan-payment
            shadows whose cash the loan re-derives
            (:func:`app.services.loan_payment_service.live_loan_transfer_amounts`).
            A MANUAL payment may also appear here when it carries a standing
            extra, and rule 4 ignores it there: the manual base is a stated
            amount and this producer reads the stored column for it, which is
            the read ruling R-FI deletes.
    """

    priced_ids: frozenset[int]
    salary_net: dict[int, Decimal]
    loan_cash: dict[int, Decimal]


def amount_basis(user_id, scenario_id, transactions) -> AmountBasis:
    """Resolve the live amounts for *transactions*, one call per producer.

    The BATCH half of the resolver.  Both producers pick their own candidates
    out of the list and ignore the rest, so a caller passes everything it
    loaded; both return an empty dict after two list comprehensions when there
    is no candidate, so a row set with neither kind pays no query.

    Calling it per row is finding **N-228**: ``live_projected_net`` runs
    ``paycheck_calculator.project_salary`` over the owner's whole pay-period
    set, because the biweekly rounding residue only reconciles against the
    complete annual figure.  One basis per read pass is what makes the per-row
    rules cheap.

    **It takes the OWNER's id rather than an ``Account``, and that is plan step
    X-au-c2's re-keying.**  The only thing it ever read off the account was
    ``account.user_id`` (the salary producer scopes its profile lookup by owner;
    the loan producer scopes by scenario alone), so requiring the object forced a
    CROSS-ACCOUNT reader -- the calendar, the spending report, a dashboard -- to
    group its rows by account and pay for one basis per group, each running the
    paycheck engine over the same pay-period set.  A ``(user_id, scenario_id)``
    pair is the real scope of both producers, so one basis now covers everything
    a reader loaded.

    Args:
        user_id: The owner whose rows are being priced; scopes the salary
            producer's profile lookup.
        scenario_id: The scenario the amounts resolve under.
        transactions: The loaded rows to price.

    Returns:
        The :class:`AmountBasis` for those rows.
    """
    # Pylint: ``import-outside-toplevel`` -- imported locally to keep the
    # income_service (paycheck/tax) and loan_payment_service (loan-resolver)
    # stacks off this module's load path and out of any import cycle, exactly as
    # ``_amounts.live_amount_overrides`` has always done; the helpers are only
    # needed at call time.
    # pylint: disable=import-outside-toplevel
    from app.services import income_service, loan_payment_service
    return AmountBasis(
        priced_ids=frozenset(txn.id for txn in transactions),
        salary_net=income_service.live_projected_net(
            user_id, scenario_id, transactions,
        ),
        loan_cash=loan_payment_service.live_loan_transfer_amounts(
            scenario_id, transactions,
        ),
    )


def amount_rule(txn) -> AmountRule:
    """Return which of R-FI's five rules owns *txn*'s amount.

    **One question to the COLUMN, then one to the DEFINITION.**  A row that
    carries no ``amount_source_id`` owns its figure and is priced by rule 1; a
    row that carries one names the RELATION that prices it, and the refinement
    inside that relation -- SALARY within a definition, LOAN_PAYMENT within a
    parent transfer -- is read live off the definition itself.  The refinement
    order is the rule: SALARY is tested before TEMPLATE because a salary profile
    names an ordinary transaction template, and LOAN_PAYMENT before TRANSFER
    because a loan payment is a transfer.  Testing them the other way round would
    place every paycheck as a template row and every loan payment as a plain
    shadow.

    **Nothing here reads ``is_override``, ``is_projected`` or ``is_deleted``, and
    that is finding N-262's fix** (plan step X-au-c2).  Those three are facts
    about whether a row COUNTS and about who last touched it, not about who owns
    its figure, and inferring ownership from them let four live doors write a row
    ``ck_transactions_amount_ownership`` admits and this dispatch refused -- the
    module docstring names all four.  What replaced them is the one statement of
    ownership the model has.  Two consequences worth stating because they used to
    be arms:

    * a row a human RE-PRICED owns its figure because the write door CLEARS its
      source and stores the typed amount, not because ``is_override`` is set --
      so the flag can go on carrying its other three facts (finding **N-238**,
      plan step X-au-h) without touching pricing;
    * a SETTLED row owns its figure because the settle FROZE it (plan step X-aq,
      formalised at X-au-c3), not because it left Projected.  Until that leaf
      lands no row is declared derived, so no settled row can reach a derived arm.

    **Soft deletion does not change the answer, deliberately.**  Being deleted is
    a statement about whether the row counts, and making it flip the rule would
    force ``amount_source_id`` to be REWRITTEN on every delete and restore -- a
    derived column beside a second writer, the shape this arc exists to remove.
    A deleted derived row resolves like any other and contributes nothing either
    way; the backfill's refusal to MINE a deleted row (migration
    ``a9d3c15e7f42``) is a question about evidence, not about ownership.

    Args:
        txn: The :class:`~app.models.transaction.Transaction` to classify.  Its
            ``template`` / ``transfer`` relationship is read only when it
            DECLARES the matching relation, so an undeclared row costs no lazy
            load at all.

    Returns:
        The :class:`AmountRule` that prices this row.

    Raises:
        KeyError: When the row names a relation this dispatch has no rule for.
            Unreachable through the FK, which admits only the seeded
            :class:`~app.enums.AmountSourceEnum` members; it is how a member
            ADDED without a rule beside it fails loudly instead of falling
            through to whichever branch happened to be last.
    """
    if txn.amount_source_id is None:
        return AmountRule.OWN
    return _RELATION_RULES[_declared_relation(txn.amount_source_id)](txn)


def _declared_relation(source_id: int) -> AmountSourceEnum:
    """Return the :class:`~app.enums.AmountSourceEnum` member *source_id* names.

    The id-to-member direction ``ref_cache`` does not publish, because every
    other consumer of a ref table compares a stored id against a cached one and
    needs no reverse map.  This dispatch is the exception: it branches on WHICH
    relation a row declared, so it must turn the stored id back into the member
    the rules are written against.  Derived from ``ref_cache.amount_source_id``
    rather than from a second query, so the two directions cannot disagree.

    Args:
        source_id: A row's stored ``amount_source_id`` (never ``None`` -- the
            caller has already tested for the OWN state).

    Returns:
        The member that id names.

    Raises:
        KeyError: When no member maps to *source_id*.  The FK to
            ``ref.amount_sources`` makes that unreachable for a seeded database.
    """
    return {
        ref_cache.amount_source_id(member): member
        for member in AmountSourceEnum
    }[source_id]


def _rule_within_definition(txn) -> AmountRule:
    """Refine the TEMPLATE relation into rule 2 or rule 3.

    A definition prices its rows either through a salary profile that names it
    or through its own effective-dated series, and which of the two is a fact
    about the DEFINITION read at this moment -- archiving the profile is what
    moves a template from the first to the second.

    ``template is None`` beside a declared relation is TEMPLATE, and that answer
    REFUSES one tier down (:func:`_stated_amount`).  A row whose definition was
    hard-deleted in this session still WAS generated by one, and asking the
    salary predicate about ``None`` would raise ``AttributeError`` -- an
    unhandled crash where every other unanswerable shape here raises the arc's
    own refusal.  Found by an adversarial review at plan step X-au-b.

    Args:
        txn: A row declaring :attr:`~app.enums.AmountSourceEnum.TEMPLATE`.

    Returns:
        :attr:`AmountRule.SALARY` or :attr:`AmountRule.TEMPLATE`.
    """
    return (
        AmountRule.SALARY
        if txn.template is not None
        and template_amount_service.is_salary_linked_template(txn.template)
        else AmountRule.TEMPLATE
    )


def _rule_within_parent_transfer(txn) -> AmountRule:
    """Refine the PARENT_TRANSFER relation into rule 4 or rule 5.

    A shadow's parent is either a loan payment -- whose cash the loan derives --
    or an ordinary transfer, and which of the two is a fact about the transfer's
    TEMPLATE (:func:`_is_loan_payment`), read live so a template switched between
    modes changes rule at that moment.

    Args:
        txn: A row declaring
            :attr:`~app.enums.AmountSourceEnum.PARENT_TRANSFER`.

    Returns:
        :attr:`AmountRule.LOAN_PAYMENT` or :attr:`AmountRule.TRANSFER`.
    """
    return (
        AmountRule.LOAN_PAYMENT if _is_loan_payment(txn.transfer)
        else AmountRule.TRANSFER
    )


def resolve_transaction_amount(txn, basis: AmountBasis) -> Decimal:
    """Return what *txn*'s amount is, by the rule that owns it.

    The resolver plan step X-au-b exists to build.  It is TOTAL over rows -- one
    of five rules applies to every one -- and total in its answer: it returns a
    ``Decimal`` or it raises, never ``None`` and never a plausible substitute.

    Proven equal to the answer the app gives today for **every one of the 997
    rows** on the 2026-08-12 production clone
    (``tests/manual/verify_amount_resolver.py``).  "The answer the app gives
    today" is not single-valued -- ``routes/grid.py:595`` reads the override map
    while ``dashboard_service.py:279`` reads the raw column, which IS finding
    **N-224** -- so the oracle grades the OVERRIDE-MAP answer, the one every
    balance surface folds.

    **It is UNWIRED as of this step.**  Nothing in ``app/`` calls it yet; plan
    step X-au-c2 routes the readers through it and X-au-c3 turns the settle
    refresh into the freeze.

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

    Args:
        txn: The :class:`~app.models.transaction.Transaction` to price.
        basis: The account's :class:`AmountBasis` (:func:`amount_basis`), built
            once for the whole row set.

    Returns:
        The row's amount as a ``Decimal``.

    Raises:
        AmountUnresolvable: When *txn* is not one of the rows *basis* was built
            over, or when the rule that owns this row cannot answer for it.  See
            the module docstring: a refusal is never a fallback.
        UndatedSettleError: Propagated from the DERIVE-mode loan arm, whose
            producer loads the loan's payment history and refuses a settled
            payment carrying no settle day
            (``balance_predicates.settled_day``).  Named here because a caller
            catching only :class:`~app.exceptions.AmountUnresolvable` would
            otherwise meet it unannounced.
    """
    if txn.id not in basis.priced_ids:
        raise AmountUnresolvable(
            f"Transaction {txn.id} was not among the rows this AmountBasis was "
            "built over, so the live producers were never asked about it. "
            "Resolving it here would answer from a basis that has no opinion: "
            "a manual loan payment would silently lose its standing extra and a "
            "salary row would be refused for the wrong reason. Build the basis "
            "from the row set you are pricing."
        )
    return _RULE_ANSWERS[amount_rule(txn)](txn, basis)


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
    rather than an omission: ``live_loan_transfer_amounts`` keys its answer on
    the SHADOW's id, so a derive-mode loan payment has no transfer-level answer
    to give.  Such a transfer therefore reaches the series arm and is REFUSED by
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
    relation = _declared_relation(xfer.amount_source_id)
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


def _is_loan_payment(xfer) -> bool:
    """Return whether *xfer* is a loan payment rather than a generic transfer.

    The fact ``loan_payment_service`` keys its whole live-derive machinery on: a
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row hanging
    off the transfer's template (decision B).  A transfer with no template, or a
    template with no settings row, is an ordinary transfer -- an investment
    contribution, a savings sweep -- and rule 5 prices it.

    Read live off the relationship rather than remembered, so a template
    switched between modes changes rule at that moment.

    Args:
        xfer: The parent :class:`~app.models.transfer.Transfer`, or ``None``
            when the shadow's parent is gone.

    Returns:
        ``True`` when a loan payment's settings drive this transfer's cash.
    """
    if xfer is None or xfer.template is None:
        return False
    return xfer.template.settings is not None


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
    return own_figure(txn.estimated_amount, "transaction", txn.id)


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

    Args:
        txn: The salary income row being priced.
        basis: The account's basis; its ``salary_net`` map holds the answer.

    Returns:
        The live net pay for the row's period.

    Raises:
        AmountUnresolvable: When the live recompute has no answer for this row.
    """
    net = basis.salary_net.get(txn.id)
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
      ``live_loan_transfer_amounts`` has already resolved into
      ``basis.loan_cash``.  Absent there means the loan would not resolve, and
      that REFUSES;
    * **manual mode** -- the operator owns the base cash, which is a STATED
      amount, so it is the definition's series (rule 5's arm, reached through
      the parent) plus the standing extra.

    **The mode is read rather than inferred, and an adversarial review is why.**
    The first draft answered "the live map when it has an entry, else the
    parent's series", which made a manual payment resolve TWO different ways:
    ``live_loan_transfer_amounts`` prices a manual payment from
    ``shadow.estimated_amount + extra`` (``_manual_shadow_amount``), so a
    payment with a standing extra was answered from the stored column while the
    same payment without one was answered from its series.  On a shadow whose
    cache had drifted from its definition the two disagreed -- ``$1,400.00``
    against ``$1,450.00`` on the review's reproduction -- and the arm that won
    was the one reading the column ruling R-FI exists to stop reading.  Now
    neither manual arm reads it.

    **The derive arm still reads the wall clock, through the producer it
    delegates to** -- ``live_loan_transfer_amounts`` resolves its basis at
    ``date.today()``, which is finding **N-40**, owned by plan step X-au-g: the
    leaf that rules a shadow's P&I onto its own due date as ruling D5 already
    put its escrow.  The README states clock-freedom as the amount model's
    precondition against the SALARY derivation, where plan step X-as closed it;
    this is the remaining read and it is disclosed rather than claimed absent.
    Dormant on production (``budget.loan_payment_settings`` is empty), so this
    rule prices ``$0.00`` there and is graded only on a seeded loan.

    Args:
        txn: The loan-payment shadow being priced.
        basis: The account's basis; its ``loan_cash`` map holds the derive-mode
            answer.

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
        live = basis.loan_cash.get(txn.id)
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
}

# WHICH RULE a declared relation refines into, keyed by the relation itself.  The
# same shape as ``_RULE_ANSWERS`` above and for the same reason: a member added
# to :class:`~app.enums.AmountSourceEnum` -- ``credit_card:CC4c``'s finance
# charge is the one already known to need one (finding **N-264**) -- raises at
# this lookup instead of silently taking whichever branch an ``if`` chain happened
# to end on.  ``tests/test_services/test_amount_source.py`` grades the table
# against the enum, so the completeness is a predicate rather than a comment.
_RELATION_RULES = {
    AmountSourceEnum.TEMPLATE: _rule_within_definition,
    AmountSourceEnum.PARENT_TRANSFER: _rule_within_parent_transfer,
}
