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
tier that actually owns it.*  :func:`standing_installment_cash` is the definition's half of that
one rule and takes the loan's contribution -- the contractual P&I and the
installment's escrow -- as arguments.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.account import Account
from app.models.transfer_template import TransferTemplate
from app.services.template_amount_service import amount_as_of, owns_its_amount
from app.utils.money import round_money


def active_recurring_transfer_template(
    account_id: int, user_id: int,
) -> TransferTemplate | None:
    """Return the active recurring transfer template paying INTO *account_id*.

    An active (``is_active``) :class:`TransferTemplate` owned by *user_id* whose
    destination is *account_id* and which carries a recurrence rule (a
    ``budget.recurrence_rules`` row names it).  The OLDEST is returned, and the
    ordering is load-bearing rather than tidy -- see the comment on it.  More
    than one recurring transfer into a single account is a user
    misconfiguration this query does not model, and ``routes/loan/
    payment_transfer.py`` handles the case rather than refusing it, so the set
    is not guaranteed to hold one row.  ``None`` when the account has no
    recurring funding transfer.
    The 1:1 ``settings`` row is eager-loaded, since the loan callers read its
    ``extra_principal`` right after (the prompt prefill and
    :func:`loan_standing_extra`).

    Args:
        account_id: The destination account (a loan or investment account).
        user_id: The owning user (scopes the query -- ownership is established by
            the caller's chokepoint).

    Returns:
        The active recurring :class:`TransferTemplate`, or ``None``.
    """
    return (
        db.session.query(TransferTemplate)
        .options(
            joinedload(TransferTemplate.settings),
            # The price SERIES, because a caller that resolves it per
            # installment (:func:`standing_installment_cash`, ~300 times for a
            # 30-year loan) would otherwise take a lazy load mid-fold.  One
            # collection per template, and the settings row beside it is loaded
            # the same way for the same reason.
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
        # **ORDERED, because ``.first()`` over an unordered query is whichever
        # row the planner hands back** -- and since plan step R7d-a this answer
        # decides how the loan's whole forward plan is PRICED, not just whether
        # a dashboard shows a prompt.  Two renders in one session could
        # otherwise disagree about a loan's payoff.  The oldest definition wins,
        # which is stable under later edits; WHICH of several recurring
        # transfers into one loan is its PAYMENT is a rule nothing states, and
        # that is finding **D47** rather than something this ORDER BY decides.
        .order_by(TransferTemplate.id)
        .first()
    )


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

    The three shapes a loan can be in are the three arms of
    :func:`standing_installment_cash`, and this value is what tells them apart.

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
            :meth:`~app.services.cash_ledger.LoanPricing.live_cash`
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


def standing_installment_cash(
    standing: "StandingPayment | None",
    contractual_pi: Decimal,
    monthly_escrow: Decimal,
    due: date,
) -> Decimal:
    """Return what one installment costs, from the loan's own definition.

    **The ONE rule for "what will this loan be paid on this date", asked about
    an installment no row covers** (plan step **R7d-a**).  A materialised row is
    priced by
    :meth:`~app.services.cash_ledger.LoanPricing.live_cash`; this is
    the same question for a month whose row has not been written, and the arms
    are deliberately the same cases so the two cannot come to disagree:

    * **No standing payment** -- the CONTRACT's P&I plus that installment's
      escrow.  Nothing else is known about how the loan will be paid.
    * **A definition that does not STATE its price**
      (:func:`~app.services.template_amount_service.owns_its_amount` is False --
      a DERIVE-mode loan payment, whose stored figure is a snapshot of the
      contract rather than a statement) -- the contract's P&I, that
      installment's escrow, and the standing extra.  That is what the mode
      MEANS, so reading the contract is the row's own rule and not a guess
      about it.
    * **A STATED price** -- what the definition's series says on the
      installment's OWN due date
      (:func:`~app.services.template_amount_service.amount_as_of`), plus the
      standing extra.  The owner has said what leaves checking that month, and
      a projection substituting the servicer's figure would model a loan the
      owner is not paying.

    **The DERIVE arm's residue is now ONE difference from what
    :meth:`~app.services.cash_ledger.LoanPricing.live_cash` prices the
    same installment at, and it is named rather than absorbed.**  It was TWO
    until plan step ``balance:X-au-g-2b``: that producer pinned its P&I at the
    READ PASS's ``as_of`` while this read the contract's P&I for the
    installment's OWN date, which differ for an ARM (finding **N-40**).  This
    tier's per-installment figure was the correct one, which is why it kept it
    rather than adopting the pin; ruling **R-IJ** made that the rule for every
    tier, so the two producers now key on the same date and the difference is
    gone.  What remains: the LAST contractual installment is a residual
    rather than the level payment
    (``amortization_engine`` forces the final month to absorb the remainder), so
    the two differ there for a reason that is not a rate effect at all; the fold
    caps that installment against the balance either way.

    **Resolved AS OF the installment, never off ``default_amount``, and an
    adversarial review of this step is why.**  That scalar is the NEWEST price
    the series states rather than the price on a date, so reading it here made
    an amount stated as effective in 2028 reach every installment from 2026
    forward: measured, a `$700.00` Van payment effective 2028-01-01 moved the
    derived payoff six installments EARLY once the future rows were absent --
    the under-generating direction R7d exists to close.  Ruling **R-FI**'s rule,
    applied to the tier that had been exempt from it.

    **An EMPTY series answers like a loan with no definition at all**, which is
    the one place this is softer than
    :func:`~app.services.cash_ledger._amount_source._stated_amount`'s refusal.
    A template owning its amount whose creator never went through
    ``set_amount`` states no price, and the honest estimate for a month nobody
    priced is the same one a loan with no recurring payment gets.  Refusing here
    would take a balance page down for a state the contract can answer.

    Args:
        standing: The loan's :func:`standing_payment`, or ``None`` when it has
            no recurring payment.
        contractual_pi: The contractual P&I governing this installment, from
            the loan's own amortization schedule.  Escrow-free by construction.
        monthly_escrow: The escrow in force for this installment
            (:func:`~app.services.escrow_calculator.escrow_monthly_as_of` on its
            due date), ``0.00`` when the loan escrows nothing.
        due: The installment's own due date -- what the stated price resolves
            AS OF.  Contract time, the same date the escrow above is resolved
            on (ruling D5).

    Returns:
        The installment's cash, escrow-INCLUSIVE where the definition states
        one -- the pairing
        :func:`~app.services.loan_ledger.split_payment_cash` takes, where the
        escrow is handed beside it and backed out of principal.
    """
    if standing is None:
        return round_money(contractual_pi + monthly_escrow)
    stated = (
        amount_as_of(standing.template, due)
        if owns_its_amount(standing.template)
        else None
    )
    if stated is None:
        return round_money(
            contractual_pi + monthly_escrow + standing.extra_principal,
        )
    return round_money(stated + standing.extra_principal)
