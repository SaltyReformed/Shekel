"""
Shekel Budget App -- Savings Dashboard: the aggregate debt summary.

What is owed across the owner's loans, what it costs each month, when the
last debt line ends and how much of the original principal is repaid -- the
:class:`DebtSummary` value object and its one construction site,
:func:`_compute_debt_summary`, with the two reducers of its own that site
composes (:func:`_accumulate_loan_debt`, :func:`_compute_principal_paid_fraction`)
and the former's owed-today predicate (:func:`_loan_ad_current_principal`);
its other two reducers are :mod:`._debt_line`'s.  Moved out of
:mod:`._metrics` below pylint's module line cap; the DTI block the summary
carries (:class:`~._metrics.DtiMetrics`) is still built there.  No Flask
imports.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.services import escrow_calculator
from app.services.savings_dashboard_service._debt_line import (
    LoanPayoffOutlook,
    debt_without_payoff_model,
    loan_payoff_outlook,
)
from app.services.savings_dashboard_service._metrics import (
    DtiMetrics,
    _dti_metrics,
)
from app.services.savings_dashboard_service._types import AccountProjection
from app.utils.money import round_money

_RATE_PLACES = Decimal("0.00001")


@dataclass(frozen=True)
class DebtSummary:
    """What is owed, what it costs, when it ends, and how much is repaid.

    THE shape of the debt summary, stated ONCE (plan step X-s3, ruling R-BD,
    finding N-106).  It was a dict assembled across four modules -- six keys
    here, three more mutated in by the DTI applier, a tenth added by a copy in
    ``dashboard_service._pulse``, an eleventh mutated in by the dashboard route
    -- so no single place said what a consumer could read, and the contract
    lived in a comment at the top of ``dashboard/_tracks.html`` because there
    was nowhere else to put it.

    **It CARRIES the payoff outlook rather than copying fields out of it**
    (ruling R-AW, applied where the copy happened).  The dict flattened
    :class:`~.._debt_line.LoanPayoffOutlook`'s two STORED fields and dropped
    the derived third, so ``is_loan_free`` -- the state that says "every loan
    you have is paid off" -- had no reader anywhere in ``app/`` and the cockpit
    footer re-derived it as a Jinja fall-through that renders nothing.  That is
    the same defect X-r deleted from the per-account projection dict one
    package over: a consumer bundle mirroring a value object field by field
    goes stale the moment the value object grows, and here it had already gone
    stale by omission.

    **FOUR questions, FOUR membership rules, and this is the ONE place they are
    stated** -- every other mention in this package cites here rather than
    restating, because a rule written down five times is a rule that can
    disagree with itself, which the first draft of this very docstring did:

    ========================  ===========================  ==================
    field                     rule                         reduces over
    ========================  ===========================  ==================
    the money figures         owed TODAY (owed > 0)        ``loan_ads``
    payoff_outlook            has a DEBT LINE ahead        ``loan_ads``
    principal_paid_fraction   ALL LOANS EVER originated    ``loan_ads``
    debt_without_payoff_date  liabilities that are NOT     ``account_data``
                              loans (no payoff model)
    ========================  ===========================  ==================

    **Three of the four share one list, and the fourth cannot.**
    :func:`~.._debt_line.debt_without_payoff_model` exists to sum what the loan
    rules exclude, so it takes the full ``account_data`` by necessity, not by
    oversight -- and it reads a SUPERSET, so it cannot disagree with the loan
    rules about a loan.  Said explicitly because the first draft of this step
    claimed "every reducer is handed that same list", which is the safety
    argument for the whole merge and was false of exactly one reducer.

    **The third rule arrived at plan step X-u** (ruling R-BS, finding N-109).
    It was a SECOND narrow producer -- the budget dashboard's tracks section ran
    the whole load -> params -> project pipeline twice per render to get it,
    measured, and the two producers had to keep agreeing on which loans count by
    inspection.  They now reduce over ONE projection of one loan set, each
    applying its own rule inside itself, so the agreement is structural; and
    because that is the exact question ruling X-q settled at a measured cost of
    19 years, the merge changed NEITHER predicate -- both reducers are the ones
    that shipped, called with the same list they were already called with.

    **On the field ``/savings`` does not render.**  ``/savings`` builds this
    summary too and has no rail to put :attr:`principal_paid_fraction` on, which
    invites the objection rulings R-BG and R-BH answered elsewhere in this
    package (a surface with no reader is deleted).  It does not apply: those two
    turned on ZERO ``app/`` readers anywhere, and this field has a live one
    (``routes/dashboard.py`` -> ``dashboard/_tracks.html``).  This value object
    was ALREADY a two-consumer union before X-u -- ``weighted_avg_rate``,
    ``debt_without_payoff_date`` and two of the outlook's three states are read only by
    ``/savings``, and the dashboard track reads a strict subset -- so the merge
    makes the union symmetric rather than adding a new class of thing.  The cost
    on ``/savings`` is one reduce over an already-built list: no query, no seam
    read.  Carrying the whole object to each consumer is ruling R-AW's rule, and
    it is what stops a field the summary grows from going missing at one end.

    Attributes:
        total_debt: Principal owed today across the loans that still owe.
        total_monthly_payments: PITI across the same loans -- the seam's
            monthly principal + interest plus each loan's escrow resolved to
            today.
        weighted_avg_rate: The principal-weighted average of the loans'
            CURRENT rates (an ARM contributes its in-effect rate), as a
            fraction quantized to five places; ``0.00000`` when nothing is
            owed.
        payoff_outlook: The seam-derived
            :class:`~.._debt_line.LoanPayoffOutlook`, carried WHOLE -- the one
            derivation the Horizon chart's flag and axis read as well.
        debt_without_payoff_date: What is owed on every liability with no
            payoff model (a card, a loan with no terms, a custom liability),
            each account's owed amount floored at zero and summed (ruling
            R-CC49), which the payoff date cannot speak for and the caption
            therefore names (plan step X-q3) -- as "with no payoff date"
            (ruling **R-CC68**, ledger row CC-361): it was ``revolving_debt``,
            captioned "revolving", until plan step credit_card:CC-5-5c,
            though two of its three kinds do not revolve.
        principal_paid_fraction: The aggregate fraction of ORIGINAL principal
            repaid across every loan that has originated, a ``Decimal`` in
            ``[0, 1]`` -- the budget dashboard's debt-rail position.  ``None``
            when no loan has originated yet, which is a state this summary can
            be in while being non-``None`` itself: a borrower whose only loan is
            a mortgage that has not closed has a debt line and a payoff date but
            has repaid nothing of anything.  It is a FRACTION, never a percent;
            the 0-100 scaling and the ``float`` cast are presentation and happen
            at the dashboard route's serialization boundary.  Its rule is the
            third row of the table above; :func:`_compute_principal_paid_fraction`
            carries WHY that rule and not another.
        dti: The :class:`~._metrics.DtiMetrics` block, or ``None`` when the user has no
            income data to compute it from.
    """

    total_debt: Decimal
    total_monthly_payments: Decimal
    weighted_avg_rate: Decimal
    payoff_outlook: LoanPayoffOutlook
    debt_without_payoff_date: Decimal
    principal_paid_fraction: Decimal | None
    dti: DtiMetrics | None


def _loan_ad_current_principal(ad: AccountProjection) -> Decimal | None:
    """Return a loan projection's contributing current balance, or None.

    The single definition of "which loan accounts contribute to the debt
    summary's owed-today aggregates" (its ``total_debt``,
    ``total_monthly_payments``, and weighted-average rate).  A loan
    contributes its seam-derived current balance when that balance is
    positive; otherwise it contributes nothing and the caller skips it.

    **The BALANCE is the whole predicate, and that is the right one for this
    question** (plan step X-q).  These three figures answer "what do you owe
    TODAY": a retired loan owes nothing, and a loan that has not been borrowed
    yet owes nothing and is not yet paying anything either -- both read
    ``$0.00`` here and both are correctly out.  The question "which loans have
    a debt line AHEAD of them" is a different one with a different set, and it
    lives at :func:`~.._debt_line.debt_line_loans`; this function used to test
    ``is_paid_off`` as well, which was the CONGRATULATION predicate answering a
    money question (finding B-16's class).  It was rescued only by the balance
    test beside it: ``is_paid_off`` implies ``is_retired`` implies the fold at
    the pass's as-of is ``<= 0``, and :attr:`~.._types.AccountProjection.owed`
    is ``owed()`` of :func:`~app.services.balance_at.balance_at` at that same
    as-of, which for an originated loan is that same fold -- so the arm could never change an
    answer.  It is deleted rather than re-pointed: a predicate that cannot
    fire reads as a rule and is not one.

    The principal-paid progress fraction does NOT use this predicate -- it sums
    over ALL loans ever originated (see
    :func:`_compute_principal_paid_fraction`), keeping retired loans in both of
    its sums so the marker stays monotonic.  The displayed debt balance, by
    contrast, is owed-today, which is exactly what this predicate scopes.

    Args:
        ad: A per-account projection carrying ``owed`` (a loan
            entry from ``_compute_account_projections``).

    Returns:
        What the loan owes today, as a positive ``Decimal``, when it
        contributes, or ``None`` when it owes nothing (zero, or a credit).
    """
    # Seam-derived (E-18 / Commit 15): the same dollar figure as the loan
    # card; replaces the previous read of the non-authoritative
    # ``LoanParams.current_principal`` column that produced F-008's
    # stored-vs-engine divergence.  OWED, not the balance: the seam reports a
    # configured loan HELD since plan step credit_card:CC-5-5c (ruling R-CC47),
    # so the principal is ``owed()`` of it -- read raw, every loan would fail
    # this test and the debt summary would read zero debt.
    principal = ad.owed
    if principal <= Decimal("0.00"):
        return None
    return principal


def _compute_principal_paid_fraction(
    loan_ads: list[AccountProjection],
) -> Decimal | None:
    """Aggregate fraction of original principal paid across ALL loans ever.

    Computes ``(sum(original_principal) - sum(owed)) /
    sum(original_principal)`` over EVERY loan the pipeline surfaces, not
    just the loans still carrying a balance.  A RETIRED loan stays in
    BOTH the numerator and the denominator, contributing
    ``Decimal("0.00")`` to the owed sum -- so its full
    ``original_principal`` lands in the "paid" portion of the numerator.

    This "all loans ever originated" basis (locked 2026-06-12 in
    ``docs/design/dashboard_card_audit.md``, Rebuild decisions item 4) is
    what makes the debt-track marker MONOTONIC: paying a single loan all
    the way off only adds its principal to the paid portion and never
    removes anything from the denominator, so the fraction can only rise,
    reaches exactly ``1`` at full payoff of every loan, and stays there --
    it never jumps backward the way the prior active-loans-only basis did
    when one loan dropped out of both sums at payoff.  The displayed
    balance label remains active-loans-only; that is
    :func:`_compute_debt_summary`'s concern, not this marker's.

    "All loans the pipeline surfaces" is, reachably, all of the user's
    NON-ARCHIVED (``is_active=True``) loan accounts that have a
    ``LoanParams`` row AND HAVE ORIGINATED.  Archived accounts are
    filtered out upstream by ``_load_dashboard_core_data``
    (``is_active=True``) and never reach the projections, so they cannot
    be included; a loan with no ``LoanParams`` row carries no
    ``original_principal`` and is likewise not a loan-ad here.  RETIRED
    loans, by contrast, remain active accounts and DO appear
    carrying a ``loan`` detail whose seam figures have
    ``is_retired`` set, so the all-loans-ever set is fully
    reachable.  The predicate is ``is_retired``
    and not ``is_paid_off`` as of plan step X-q: "this loan owes nothing" is
    the question this marker asks, and a loan retired by a lump-sum true-up
    answers it whether or not the ledger can BADGE it.  A loan the user has
    configured but not yet BORROWED (a mortgage closing next month) is
    excluded from both sums -- see the loop.

    ``original_principal`` is a NOT NULL, ``> 0`` column on
    :class:`~app.models.loan_params.LoanParams`, so any real loan-ad
    supplies a positive denominator.  ``None`` is returned when NO loan has
    originated (the denominator would be zero) -- which includes both "the user
    has no loans" and "every loan the user has is still unborrowed"; a fully
    paid-off loan set returns ``Decimal("1")``, not ``None``.

    **The 2026-06-12 ruling cited above gets that last part WRONG, and it is
    cited anyway because it is the authority for the BASIS, not for the null
    case.**  It says "None (rail renders without a marker) only when the user has
    no loans at all" (``dashboard_card_audit.md``, same item).  The unborrowed
    loan the ``is_originated`` skip below excludes -- which post-dates that
    ruling by three weeks (plan step X-o, finding N-98) -- is the second way the
    denominator reaches zero, and a reader who follows the citation must not
    take the null clause with it.  Pinned at
    ``TestPrincipalPaidFraction::test_fraction_none_when_every_loan_is_unborrowed``.

    Args:
        loan_ads: The projections that carry a ``loan`` detail -- the loan
            subset of ``_compute_account_projections`` output, ALREADY
            filtered, exactly as :func:`_accumulate_loan_debt` takes it.  It
            selected that subset itself until plan step X-u, back when its
            caller was a producer of its own; taking the filtered list is what
            lets :func:`_compute_debt_summary` state "which projections are
            loans" ONCE for all THREE of the reducers it hands that list to, so
            the loan membership rules can differ without the loan SET being able
            to.  An unfiltered list is not silently tolerated: the loop below
            dereferences ``ad.loan.figures`` with no guard, so a non-loan raises
            immediately.

    Returns:
        The principal-paid fraction as a ``Decimal`` in ``[0, 1]`` (a
        loan whose current balance somehow exceeds its original principal
        is clamped to ``0`` so the marker never renders to the left of the
        rail), or ``None`` when no loan has originated.
    """
    total_original = Decimal("0.00")
    total_current = Decimal("0.00")
    for ad in loan_ads:
        # A loan that has not been BORROWED yet is in NEITHER sum.  Its principal
        # is not money the user owes, and none of it has been repaid.  Counting it
        # would put its full original principal in the denominator against a
        # current balance of $0.00 -- the seam's correct answer for a debt that
        # does not exist yet -- and report every cent of it as PAID: an unclosed
        # $200,000 mortgage beside a never-paid $100,000 auto loan read 66.67%
        # repaid on a borrower who had repaid nothing.  It would also break this
        # marker's one design invariant below: the fraction would COLLAPSE from
        # 66.67% to 0% on closing day, when the mortgage's balance steps from
        # $0.00 to $200,000.
        if not ad.loan.figures.terms.is_originated:
            continue
        # ALL loans ever: every loan-ad contributes its original
        # principal to the denominator.  A RETIRED loan contributes
        # Decimal("0.00") to the current-balance sum (regardless of the
        # resolver's as-of-today figure) so its full principal counts as
        # paid; a loan that still owes contributes its seam-derived current
        # balance, never below zero.  The predicate is ``is_retired`` and not
        # ``is_paid_off`` (plan step X-q): "this loan owes nothing" is the
        # question here, and a loan retired by a lump-sum true-up with no
        # payment rows answers it -- it is simply not BADGED.  The two agree on
        # the figure either way (a retired loan's balance folds to <= $0.00,
        # so the ``max(current, 0)`` below adds exactly nothing on either
        # predicate), which is why this is a vocabulary fix and not a
        # behaviour change.
        total_original += ad.loan.params.original_principal
        if ad.loan.figures.is_retired:
            continue
        # What it owes (plan step credit_card:CC-5-5c: the balance is HELD, so
        # read raw a loan would floor to 0 here and read as fully paid).
        total_current += max(ad.owed, Decimal("0.00"))

    if total_original <= Decimal("0.00"):
        return None

    fraction = (total_original - total_current) / total_original
    # A current balance above the original principal (negative paid
    # fraction) is meaningless for a payoff marker; clamp to 0.
    if fraction < Decimal("0"):
        return Decimal("0")
    return fraction


def _accumulate_loan_debt(
    loan_ads: list[AccountProjection], escrow_map: dict[int, list],
    as_of: date,
) -> tuple[Decimal, Decimal, Decimal]:
    """Sum the owed-today debt metrics across the loans that still owe.

    Walks the per-account loan projections, skipping any whose seam-derived
    current balance is zero or negative, and accumulates the running totals the
    debt summary reports.

    **It no longer collects payoff dates** (plan step X-q).  It used to derive
    the debt-free date inside this loop, over the loans that owe money TODAY --
    a different set from the loans that still have a debt line, and the
    difference is a mortgage that has not closed yet: it owes ``$0.00``, so it
    was skipped here and the caption reported the date the OTHER loans finish,
    19 years early on the developer's own data (finding N-98).  The date now
    comes from :func:`~.._debt_line.loan_payoff_outlook`, which the Horizon
    chart reads as well.

    Args:
        loan_ads: Per-account projections that carry a ``loan`` detail (the
            loan subset of ``_compute_account_projections`` output).
        escrow_map: Dict mapping account_id to list of EscrowLine (with versions).
        as_of: The read pass's day, which resolves each escrow LINE to its
            active version.  It was ``date.today()`` here until pay-calendar
            plan step C2-f2d-3 (ledger row **P55**) -- a bare clock read
            deciding which escrow version prices the PITI total that the DTI
            ratio beside it divides, on a page whose every other figure is
            measured at the pass's day.

    Returns:
        ``(total_debt, total_monthly, weighted_rate_sum)`` -- the running sums.
    """
    total_debt = Decimal("0.00")
    total_monthly = Decimal("0.00")
    weighted_rate_sum = Decimal("0.00")

    for ad in loan_ads:
        principal = _loan_ad_current_principal(ad)
        if principal is None:
            continue

        # DH-#56: the loan's CURRENT rate (resolver-derived,
        # ``state.current_rate``), replacing the retired
        # ``LoanParams.interest_rate`` column.  weighted_avg_rate now
        # reflects the rate the loan is actually accruing at today --
        # for a changed ARM the in-effect rate, not the stale origination
        # value the dropped column had drifted from.
        rate = ad.loan.figures.terms.current_rate
        monthly_pi = ad.loan.figures.terms.monthly_payment

        # Include escrow (property tax, insurance) for PITI total, resolved to
        # today's active version per line via the shared as-of function.
        lines = escrow_map.get(ad.account.id, [])
        monthly_escrow = escrow_calculator.escrow_monthly_as_of(lines, as_of)
        monthly_total = round_money(monthly_pi + monthly_escrow)

        total_debt += principal
        total_monthly += monthly_total
        weighted_rate_sum += rate * principal

    return total_debt, total_monthly, weighted_rate_sum


def _compute_debt_summary(
    account_data: list[AccountProjection],
    escrow_map: dict[int, list],
    gross_monthly: Decimal,
    as_of: date,
) -> DebtSummary | None:
    """Compute aggregate debt metrics across the user's loan accounts.

    THE one construction site for :class:`DebtSummary` (plan step X-s3): every
    field, including the DTI block, is set here.  The DTI keys used to be
    MUTATED in afterwards by a separate applier, so the object a template read
    was never fully built anywhere and "which fields does a debt summary have"
    was answerable only by reading the modules in call order.

    Uses per-account data already computed by _compute_account_projections:
    ``owed`` directly, the original principal, payment and rate off
    the ``loan`` detail's contract row and seam figures (plan steps X-r /
    X-t1), and the payoff through
    :func:`~.._debt_line.loan_payoff_outlook`.  Escrow components are loaded
    separately and included in the monthly total so DTI reflects PITI
    (principal, interest, taxes, insurance).

    **Four figures, four membership rules, answered in four places on purpose**
    (plan steps X-q and X-u).  :class:`DebtSummary`'s docstring is where those
    rules are stated; this function is where they are APPLIED, and the only
    thing worth repeating here is what the application guarantees.

    **The three LOAN rules are safe because they share one list.**  ``loan_ads``
    is computed once below and handed to each of them, so a loan rule can differ
    in what it does with a loan and never in which loans it was shown.  (The
    fourth figure, ``debt_without_payoff_date``, is about the liabilities that are NOT
    loans and takes ``account_data`` -- a superset, so it cannot disagree with
    the three about a loan.  The table in :class:`DebtSummary` says which is
    which.)  The fraction reached this function at plan step X-u (finding N-109)
    from a producer that ran its own load and its own projection to build its
    own list -- two lists that agreed because two docstrings said they must.

    Deriving the payoff date here, over the owed-today set, is what put a
    19-year contradiction between this caption and the Horizon's flag on one
    page (finding N-98); it comes from
    :func:`~.._debt_line.loan_payoff_outlook` instead, the ONE derivation that
    flag reads as well.  The outlook is carried WHOLE rather than flattened into
    fields, which is ruling R-AW; see :class:`DebtSummary`.

    Args:
        account_data: The per-account projections from
            _compute_account_projections.
        escrow_map: Dict mapping account_id to list of EscrowLine (with versions).
        gross_monthly: The engine-derived gross MONTHLY income the DTI block
            is computed from -- the owner's paycheck converted at their own
            cadence by the caller; ``0.00`` when the user has no salary data,
            which is what makes :attr:`DebtSummary.dti` ``None``.
        as_of: The read pass's day, which resolves each loan's escrow version
            inside :func:`_accumulate_loan_debt` (ledger row **P55**).

    Returns:
        The :class:`DebtSummary`, or ``None`` if no loan accounts with params
        exist -- so a user whose only liability is a card has no payoff caption
        to qualify, which is why the ``debt_without_payoff_date`` caveat rides here.
    """
    loan_ads = [ad for ad in account_data if ad.loan is not None]
    if not loan_ads:
        return None

    total_debt, total_monthly, weighted_rate_sum = (
        _accumulate_loan_debt(loan_ads, escrow_map, as_of)
    )

    if total_debt > Decimal("0.00"):
        weighted_avg_rate = (weighted_rate_sum / total_debt).quantize(
            _RATE_PLACES, rounding=ROUND_HALF_UP
        )
    else:
        weighted_avg_rate = Decimal("0.00000")

    total_monthly_payments = round_money(total_monthly)
    return DebtSummary(
        total_debt=round_money(total_debt),
        total_monthly_payments=total_monthly_payments,
        weighted_avg_rate=weighted_avg_rate,
        # The seam-derived outlook, carried WHOLE (ruling R-AW).  Flattening it
        # dropped ``is_loan_free`` -- "every loan you have is paid off" -- and a
        # consumer cannot miss a field that was never copied, so the cockpit
        # footer fell through that state in silence for as long as the copy
        # existed.  Its other two states are the ones the caller must SAY rather
        # than omit: a debt-line loan that never clears at its current payment is
        # a different fact from having no loans left, and the loan detail page
        # already names it in words on the same condition ("No payoff at current
        # payment", plan C8d).
        payoff_outlook=loan_payoff_outlook(loan_ads),
        # What the payoff date CANNOT speak for (plan step X-q3, finding
        # N-99): every liability with no PAYOFF model -- a card, whose forward
        # balance is its cash fold since plan step credit_card:CC-1 but which
        # no schedule pays off, a loan with no terms, a custom liability -- is
        # invisible to the derivation, so the caption says so instead of
        # implying the user is out of debt on a date that only covers their
        # loans (ruling R-CC68: "with no payoff date").
        debt_without_payoff_date=debt_without_payoff_model(account_data),
        # The debt rail's position (plan step X-u, ruling R-BS, finding N-109).
        # It reduces over the SAME ``loan_ads`` the two loan rules above do and
        # applies its own all-loans-ever rule inside itself, so the rules stay
        # distinct while the loan SET stays one.  It was a second producer that
        # re-ran this whole pipeline to get here, which is the redundancy the
        # finding measured; what it is NOT is a re-decision of any rule.
        principal_paid_fraction=_compute_principal_paid_fraction(loan_ads),
        dti=_dti_metrics(total_monthly_payments, gross_monthly),
    )
