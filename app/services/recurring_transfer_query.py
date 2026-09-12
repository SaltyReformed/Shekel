"""The recurring transfer funding an account, and what that DEFINITION says.

A single-responsibility leaf helper for the one query three surfaces share --
"does an active recurring transfer template pay INTO this account, and if so
which one?" -- and for the facts read off the template it returns.  Its only
service import is ``template_amount_service``, itself a leaf over ``db`` and
two models, so the graph stays acyclic: every consumer of THIS module imports
it, and none of them is reachable from there.  The loan and investment
dashboards use the query to decide
whether to show the set-up-a-recurring-payment prompt, and the loan
recurrence-sync (Risk R-4) uses it to find the rule whose ``end_date`` it bounds
to the projected payoff.  Centralising it keeps those surfaces from drifting on
what counts as an account's recurring funding transfer.

**The loan-payment SETTINGS reads moved here at plan step R7d-a**, from
``loan_payment_service``.  They are reads OF this module's own subject -- the
mode a payment is in, the base it states, the standing extra it carries -- and
they were three call sites away from the query that finds the template they read.
That module was at pylint's 1000-line ceiling exactly, so the move is also what
stops the next fact about a definition being paid for by a ``too-many-lines``
disable; a module's line count going over is a statement that it holds more than
one subject, and this was the second one.

What is deliberately NOT here is how a MATERIALISED row is priced
(``cash_ledger.LoanPricing``): that needs the loan resolved, its rate
periods and its escrow history, which is the AMOUNT MODEL's work -- rule 4 --
and not a read of a definition.  *That rule's producer lived in
``loan_payment_service`` until plan step X-au-g-2a moved it into
``cash_ledger``; this sentence said "the loan seam's work" and now names the
tier that actually owns it.*  **The definition's half of that rule lived here
too, as ``standing_installment_cash``, until plan step R16-b-2 deleted it**
(ruling **R-R67**): it was a copy of the amount model's own arms, measured a
cent apart from them on a loan's last installment (finding **REC-517**), and
an estimate is priced through those arms now
(:func:`app.services.cash_ledger.definition_cash`).
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.account import Account
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate


def destination_account(
    template: TransferTemplate | TransactionTemplate,
) -> Account | None:
    """Return the account *template* pays into, or ``None`` when it pays into none.

    **The COLUMN, then a lookup -- never ``template.to_account``**, and an
    adversarial review of plan step R7d-b measured why.  That relationship is
    ``lazy="joined"``, which loads it with the template and then does NOT
    refresh it when the FK column is written: measured on SQLAlchemy 2.0.49,
    a ``setattr(template, "to_account_id", other)`` leaves ``to_account``
    pointing at the OLD account through the following ``flush()`` and only
    re-loads at ``commit()``.  ``routes/transfers/templates.py`` writes
    exactly that -- ``to_account_id`` is in ``_TEMPLATE_UPDATE_FIELDS`` and
    is assigned by ``setattr`` -- and then REGENERATES before committing, so
    a resolver reading the relationship would bound the new destination's
    rows by the OLD loan's payoff.  A pending template is the second state:
    its ``to_account_id`` is set and its ``to_account`` is still ``None``.
    ``db.session.get`` costs nothing when the row is already in the identity
    map, which is the case the joined load creates anyway.

    ONE spelling, shared by
    :func:`app.services.loan_recurrence_sync.loan_payment_window` and
    :func:`app.services.balance_at.is_standing_loan_payment` (plan step R7d-f;
    the first cut carried the read twice in one module and an adversarial
    review named it).  **It lived in ``loan_recurrence_sync`` as a private
    until plan step R16-b-2** moved the identity it serves into the balance
    seam (ruling **R-R70**): that module imports the seam, so the seam could
    not reach the read there, and a definition's destination is a fact about
    the DEFINITION -- this module's subject -- rather than about a loan's
    window.

    Args:
        template: A ``TransferTemplate``, or a ``TransactionTemplate``, which
            carries no ``to_account_id`` at all -- ``getattr`` on the FK
            column is what keeps both readers kind-agnostic.

    Returns:
        The destination :class:`~app.models.account.Account`, or ``None`` when
        the template pays into no account or names one not yet flushed.  The
        second is unreachable for a persisted definition -- ``to_account_id``
        is NOT NULL under an ``ON DELETE RESTRICT`` foreign key -- and a
        pending one generates nothing either way.
    """
    account_id = getattr(template, "to_account_id", None)
    if account_id is None:
        return None
    return db.session.get(Account, account_id)


def active_recurring_transfer_templates(
    account_id: int, user_id: int,
) -> list[TransferTemplate]:
    """Return EVERY active recurring transfer template paying INTO *account_id*.

    The active (``is_active``) :class:`TransferTemplate` rows owned by
    *user_id* whose destination is *account_id* and which carry a recurrence
    rule (a ``budget.recurrence_rules`` row names each), oldest first.  **The
    set, not one of it** (plan step **R16-b-2**, ruling **R-R35**): every
    recurring transfer into a loan is a payment against it, and the balance
    seam's ESTIMATED tier sums each one's occurrences on its own cadence --
    so the question this answers is "which definitions pay in here", where
    :func:`active_recurring_transfer_template` below answers the narrower
    "which ONE is the standing payment" for the three readers that still
    need a single row.  That function is this one's first element, so the
    filter is stated once.

    The 1:1 ``settings`` row and the price SERIES are eager-loaded on every
    row -- see the comments on the options for why.

    Args:
        account_id: The destination account (a loan or investment account).
        user_id: The owning user (scopes the query -- ownership is established
            by the caller's chokepoint).

    Returns:
        The active recurring :class:`TransferTemplate` rows, ascending by id;
        ``[]`` when the account has no recurring funding transfer.
    """
    return (
        db.session.query(TransferTemplate)
        .options(
            joinedload(TransferTemplate.settings),
            # The price SERIES, because a caller that resolves it per
            # occurrence (the balance seam's ESTIMATED tier through
            # ``cash_ledger.definition_cash``, ~300 times for a 30-year loan)
            # would otherwise take a lazy load mid-fold.  One collection per
            # template, and the settings row beside it is loaded the same way
            # for the same reason.
            joinedload(TransferTemplate.amount_versions),
        )
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.to_account_id == account_id,
            TransferTemplate.is_active.is_(True),
            # "Carries a recurrence rule", as an EXISTS rather than a NOT NULL
            # column test: the owning FK moved onto the rule at plan step
            # R-F6, so what used to be ``recurrence_rule_id IS NOT NULL`` here
            # is now a row on the other side.
            #
            # ``uq_recurrence_rules_transfer_template_id`` covers the join
            # column, so the subquery is index-ABLE -- which is a statement
            # about what happens as the table grows, not about today.  Measured
            # on a production clone (6 transfer templates, 43 rules), the
            # planner chooses a nested loop over sequential scans and is right
            # to: an index probe costs more than reading two tiny tables.  The
            # earlier comment here claimed the probe as fact and an adversarial
            # review measured otherwise.
            TransferTemplate.recurrence_rule.has(),
        )
        # **ORDERED, because the first row of an unordered query is whichever
        # row the planner hands back** -- and from plan step R7d-a to R16-b-2
        # that answer decided how the loan's whole forward plan was PRICED.
        # It prices nothing now (the tier sums every row here, finding
        # **D47**), but it still decides the loan-payment identity the form
        # locks on and the opening-bound sync writes for, and two renders in
        # one session must not disagree about it.  The oldest definition wins,
        # which is stable under later edits.
        .order_by(TransferTemplate.id)
        .all()
    )


def active_recurring_transfer_template(
    account_id: int, user_id: int,
) -> TransferTemplate | None:
    """Return the active recurring transfer template paying INTO *account_id*.

    The OLDEST of :func:`active_recurring_transfer_templates`, and the
    ordering is load-bearing rather than tidy -- see the comment on it there.
    More than one recurring transfer into a single account is a state
    ``routes/loan/payment_transfer.py`` handles rather than refusing, so the
    set is not guaranteed to hold one row.  ``None`` when the account has no
    recurring funding transfer.

    **Since plan step R16-b-2 this row PRICES nothing** -- the ESTIMATED tier
    sums every definition (:func:`active_recurring_transfer_templates`) --
    and what it still decides is the loan-payment IDENTITY the form locks on
    and the opening-bound sync writes for
    (:func:`app.services.balance_at.is_standing_loan_payment`, plan ledger
    row **D50**), plus the three loan-side readers that need ONE row: the
    dashboard's extra-principal prefill and the two routes that MUTATE a
    settings row (**D49**).  Ruling **R-R35** wants that search deleted
    rather than answered; those readers are what keep it.

    Args:
        account_id: The destination account (a loan or investment account).
        user_id: The owning user (scopes the query -- ownership is established
            by the caller's chokepoint).

    Returns:
        The oldest active recurring :class:`TransferTemplate`, or ``None``.
    """
    templates = active_recurring_transfer_templates(account_id, user_id)
    return templates[0] if templates else None


def loan_standing_extra(account_id: int, user_id: int) -> Decimal:
    """Return a loan's standing monthly overpayment (``0.00`` when none).

    The ``extra_principal`` on the loan's active recurring payment's
    ``loan_payment_settings`` row -- the single loan-level figure the payoff
    projection threads so the committed trajectory and payoff date reflect the
    real plan (step 5).  ``Decimal("0.00")`` when the loan has no recurring
    payment, or one with no settings row (a legacy manual payment).

    **One field of :func:`standing_payment`, and it reads it rather than
    repeating it** (plan step R7d-a).  It stated the row-absent default a third
    time -- ``template.settings is None -> 0.00`` -- beside
    :func:`loan_payment_config`, which exists to state exactly that once.

    Args:
        account_id: The loan account whose standing extra to read.
        user_id: The owning user (scopes the lookup).

    Returns:
        The standing ``extra_principal`` ``Decimal``, or ``Decimal("0.00")``.
    """
    standing = standing_payment(account_id, user_id)
    return Decimal("0.00") if standing is None else standing.extra_principal


def loan_standing_extra_for_account(account_id: int) -> Decimal:
    """Return a loan's standing overpayment, resolving the owner from the account.

    The account-scoped form of :func:`loan_standing_extra` for callers that hold
    only ``account_id``, and it derives the owning user from the account (one PK
    lookup) before reading the active recurring payment's ``extra_principal``.
    ``Decimal("0.00")`` when the account does not exist or has no recurring loan
    payment.

    **The balance seam stopped calling it at plan step R7d-a** -- its resolver
    bundle takes the WHOLE :func:`standing_payment` now, since the forward plan
    needs the definition and not just one field of it, and reads the extra off
    that. What is left here is ``tests/manual/verify_loan_daily_figures.py``,
    the by-hand loan probe.

    Args:
        account_id: The loan account whose standing extra to read.

    Returns:
        The standing ``extra_principal`` ``Decimal``, or ``Decimal("0.00")``.
    """
    account = db.session.get(Account, account_id)
    if account is None:
        return Decimal("0.00")
    return loan_standing_extra(account_id, account.user_id)


def loan_payment_config(template: TransferTemplate) -> tuple[bool, Decimal]:
    """Return ``(derive_from_loan, extra_principal)`` for a transfer template.

    The single accessor for a recurring transfer's loan-payment settings
    (:class:`~app.models.loan_payment_settings.LoanPaymentSettings`, decision B),
    which live in a 1:1 table rather than on the generic template.  A template
    with NO settings row is not a loan payment: ``derive_from_loan`` defaults
    ``False`` and ``extra_principal`` ``Decimal("0.00")``, so the live-derive and
    overpayment machinery stays dormant for every investment contribution and
    generic transfer.  The ``settings`` relationship must already be loaded by
    the caller (the readers ``joinedload`` it) so this stays a pure in-memory
    read with no N+1.

    **It became PUBLIC at plan step X-au-b**, and the alternative was a second
    copy.  The amount resolver
    (:mod:`app.services.cash_ledger._amount_source`) has to know which MODE a
    loan payment is in before it can price it -- a derive-mode payment resolves
    from the loan, a manual one from its definition plus this ``extra`` -- and
    reading ``template.settings`` there would be a second spelling of the
    row-absent defaults this function exists to state once.  An adversarial
    review found the resolver answering a manual payment two different ways for
    want of exactly this.

    Args:
        template: The :class:`~app.models.transfer_template.TransferTemplate`
            whose loan-payment settings to read.

    Returns:
        ``(derive_from_loan, extra_principal)`` -- the settings row's values, or
        ``(False, Decimal("0.00"))`` when the template has no settings row.
    """
    settings = template.settings
    if settings is None:
        return False, Decimal("0.00")
    return settings.derive_from_loan, Decimal(str(settings.extra_principal))


@dataclass(frozen=True)
class StandingPayment:
    """What a loan's STANDING recurring payment says one installment costs.

    The loan-level answer to "what is this loan going to be paid each month",
    read off the definition rather than off any row the definition has already
    generated -- which is the whole reason it exists (plan step **R7d-a**).  The
    forward plan has to price an installment for a month whose row has not been
    written yet, and it used to guess the CONTRACT there while the row it was
    guessing about would carry this.  A guess that disagrees with the row makes
    a loan's payoff depend on whether the rows happen to have been materialised,
    which is a loop: the payoff bounds the recurrence, the recurrence writes the
    rows, and the rows move the payoff.

    **It PRICES nothing since plan step R16-b-2** (ruling **R-R67**): the
    forward plan sums every definition into the loan and prices each
    occurrence through the amount model's own arm.  What it still carries is
    the extra the resolver's committed schedule threads and the identity the
    recurrence form locks on (:func:`app.services.balance_at.is_standing_loan_payment`).

    **It carries the TEMPLATE and not a price, and that is the correction an
    adversarial review of this step forced.**  The first cut carried
    ``template.default_amount``, which is not what the definition costs on a
    date: :func:`~app.services.template_amount_service._resync_scalar` puts that
    column on **the NEWEST price the series states**, deliberately not today's,
    and ``current_amount`` exists because an edit form asking the wrong one of
    those two was already a defect once.  Pricing three hundred future
    installments off it makes an amount stated as effective in 2028 reach every
    installment from 2026 forward -- measured on a production clone, an owner
    stating ``$700.00`` effective 2028-01-01 moved the Van Loan's derived payoff
    from `2029-01-22` to `2028-07-22` when its future rows were absent, six
    installments in the UNDER-generating direction this step exists to close.
    Holding the template instead lets the price resolve AS OF the installment
    (:func:`~app.services.template_amount_service.amount_as_of`), which is
    ruling **R-FI**'s rule and what every other reader of a stated amount does.

    Attributes:
        template: The loan's active recurring payment definition.  Its price is
            resolved per installment rather than read as a scalar, and whether
            it states a price at all is
            :func:`~app.services.template_amount_service.owns_its_amount`'s
            question -- False for a DERIVE-mode payment, whose stored figure is
            a snapshot of the contract rather than a statement.
        extra_principal: The standing monthly overpayment (``0.00`` when none),
            added in BOTH modes exactly as
            amount rule 4 (:func:`app.services.cash_ledger.resolve_transaction_amount`)
            adds it to a materialised row.
    """

    template: TransferTemplate
    extra_principal: Decimal


def standing_payment(
    account_id: int, user_id: int,
) -> "StandingPayment | None":
    """Return what *account_id*'s standing recurring payment says, or ``None``.

    ONE read of the loan's payment definition, where
    :func:`~app.services.recurring_transfer_query.loan_standing_extra` reads a
    single field of the same row: the mode and the stated base are needed
    beside the extra the moment anything has to price an installment the
    definition has not generated yet.

    ``None`` when the loan has no active recurring payment at all -- a loan the
    owner pays by hand, or has not set up yet.  That is a THIRD state and not a
    zeroed :class:`StandingPayment`: "no definition" means the contract is the
    only estimate there is, where a definition stating ``0.00`` would mean the
    owner plans to pay nothing.

    Args:
        account_id: The loan account whose standing payment to read.
        user_id: The owning user (scopes the lookup, as the shared query
            requires).

    Returns:
        The :class:`StandingPayment`, or ``None``.
    """
    template = active_recurring_transfer_template(account_id, user_id)
    if template is None:
        return None
    _derive, extra = loan_payment_config(template)
    return StandingPayment(template=template, extra_principal=extra)
