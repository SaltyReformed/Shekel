"""Balance-at-T seam -- the LIABILITY view (multi-date, forward-only).

The seam's third shape, beside the period-keyed maps (:mod:`._kind_correct`)
and the scalar-at-a-date: every liability's owed magnitude at a list of FORWARD
calendar dates, answered in ONE loan-resolution pass.

It exists because a long-horizon liability band needs each debt's owed balance
at ~25 annual sample dates, and the caller should not have to know which forward
rule each liability takes.  It reads the seam's ONE kind-correct multi-date
producer (:func:`._kind_correct.balance_at_dates`) once per liability over the
whole future sample axis -- a configured loan's amortization ``positions``, every
other liability's cash fold -- so the band cannot drift from the balance the rest
of the app reports, and no consumer holds a balance-at-T boundary rule the seam
exists to keep out of consumer hands
(``docs/audits/balance_architecture/archive/followup_fence_loan_owed_at_dates.md``,
a historical record of how the view came to be).

**This module holds NO kind dispatch of its own** (plan step credit_card:CC-1,
ruling R-CC14).  Until that step it asked :func:`._resolution.configured_loan`
itself and held every non-loan liability FLAT at its current owed magnitude,
"because it has no forward model" -- false since plan step X-g2b gave the cash
fold a PLANNED tier for every account, and the reason a Credit Card's projected
balance never reached the net-worth horizon.  Two rules are left here: the
SPLICE (today reads the caller's confirmed figure, the future reads the
producer) and the NO-BASELINE hold (no scenario, no plan to fold, every
liability flat), which predates CC-1 and is named in the function's docstring.

**It is also the home of the seam's ONE sign flip**, :func:`owed` (plan step
credit_card:CC-5-5a, ruling R-CC47): what an account owes is minus the balance
it holds.  The flip was ``card_statement.owed`` (ruling R-CC29), written for a
statement producer no ``app/`` module calls yet; R-CC47 moved it here so the
net-worth surfaces and that producer read ONE flip rather than two spellings
of it.
"""

from datetime import date
from decimal import Decimal

from app.models.account import Account
from ._context import BalanceContext

from ._inputs import ZERO
from ._kind_correct import balance_at_dates


def owed(balance: Decimal) -> Decimal:
    """Return what an account OWES for the balance it HOLDS -- the ONE sign flip.

    A balance here is what the account holds: positive for money in it,
    negative for money owed on it.  So what it owes is ``-balance``: positive
    when the owner owes, NEGATIVE when the account holds a credit -- a card the
    issuer owes ``$50.00`` reads ``+50.00`` held and ``-50.00`` owed, which is
    the answer ledger row CC-354 says the net-worth surfaces get wrong.

    **Ruling R-CC29 ruled the flip for the card statement** ("ONE sign flip, in
    the module every consumer reads"), and it lived in
    :mod:`app.services.card_statement` as ``owed`` until plan step
    credit_card:CC-5-5a moved it here under ruling **R-CC47** ("'Owed' is minus
    the balance, R-CC29's one flip moved into the balance seam").  A statement
    producer will state its figures as ``owed(cash_balance_at(...))`` (none is
    wired in ``app/`` yet); today's one reader is the savings cockpit's
    revolving-debt footer
    (:func:`app.services.savings_dashboard_service._debt_line.debt_without_payoff_model`,
    ruling R-CC49).

    **Which balances are HELD today, precisely** -- in the seam's arithmetic.
    The kind-correct seam's for every account that is NOT a configured loan (a
    Credit Card, a loan with no ``LoanParams``, a custom liability, every
    asset), and the cash fold's for every account that is NOT a configured loan.
    Two exclusions, both measured on the production-shape clone at CC-5-5a:

    * A CONFIGURED loan's kind-correct balance
      (:func:`~app.services.balance_at.balance_at` and the period maps) is
      reported as an OWED figure -- the Mortgage's ``176,719.77`` -- which is why
      :func:`liability_owed_at_dates` and the net-worth hero still take ``abs``
      rather than this.  Plan step credit_card:CC-5-5c re-signs that arm to the
      held sign (R-CC47) and moves those readers onto this flip.
    * A configured loan's CASH fold is not a balance of the loan in EITHER
      sign, and never becomes one: it is the account-level assertion as typed
      plus each whole payment INTO the loan (interest and escrow included),
      with no interest accrued -- the same Mortgage's ``cash_balance_at`` reads
      ``+185,747.21``, and the Van Loan's ``2,127.76`` is its ``0.00``
      assertion plus four ``531.94`` payments.  Plan step CC-5-5c re-signs the
      kind-correct arm only, so this stays true after it: a configured loan's
      cash fold is NEVER an input to this function.

    Until CC-5-5c a caller must not hand this the first figure, and it must
    never hand it the second.  **What an owner TYPED is a further question the
    arithmetic cannot answer**: of the doors that take a liability's balance,
    the create form asks for "The account's real-world balance." with no sign,
    the grid's anchor editor carries no help text at all, and only the
    books-opening door says "Negative for something you owe." -- and the
    Mortgage above was typed positive, so a liability with no loan terms
    carries whatever sign was entered.  The developer ruled the
    remedy after CC-5-5a's review (ruling R-CC52): those doors ask for the
    amount OWED and store the held sign through this flip (plan step
    credit_card:CC-5-5b), so the premise is true by construction rather than
    by the owner's reading of a help text.

    Args:
        balance: A HELD balance (see above for which balances are).

    Returns:
        ``-balance``: positive when the owner owes, negative when the account
        holds a credit, and an unsigned ``0.00`` at zero (``decimal`` negates a
        zero to a positive zero outside ``ROUND_FLOOR``, which nothing here
        sets).
    """
    return -balance


def _spliced_owed_series(
    sample_dates: list[date],
    today: date,
    current: Decimal,
    owed_by_date: dict[date, Decimal],
) -> list[Decimal]:
    """Splice the confirmed present with the forward projection, per sample date.

    A date at or before *today* reads *current* -- the ledger-confirmed balance
    the caller supplied, which is the figure the net-worth hero renders -- and a
    strictly-future date reads its OWN projected value out of *owed_by_date*.

    The join is BY DATE, not by position.  An earlier draft consumed the forward
    producer's list positionally, which was correct only because it happened to
    build the list in the caller's order with no sort and no dedupe -- an unwritten
    cross-module contract that a future "harmless" tidy-up (sorting or
    de-duplicating the sample dates before the expensive schedule walk) would have
    broken SILENTLY, mis-valuing every point of the liability band with no crash and
    no failing test.  :func:`~app.services.balance_at.positions` now returns a
    date-keyed dict, so keying on the date here makes that state impossible to
    reach by construction.

    ``abs`` is applied to the projected value for the same reason it is applied
    to *current*: this view's contract is a POSITIVE owed magnitude at every
    date.  A schedule row's ``remaining_balance`` is non-negative, but the
    folded balance has no zero floor (an overpaid payoff folds negative) -- so
    without this an overpaid loan would ADD its overpayment to the liability
    band today and SUBTRACT it at every future point.

    Args:
        sample_dates: The dates to build the series over (the output order).
        today: The present-vs-future boundary (the caller's as-of date).
        current: The liability's current owed magnitude (the today value,
            already an absolute magnitude).
        owed_by_date: The projected owed balance keyed by each strictly-future
            date among *sample_dates*.

    Returns:
        The owed magnitude at each of *sample_dates*.
    """
    return [
        abs(owed_by_date[sample_date]) if sample_date > today else current
        for sample_date in sample_dates
    ]


def liability_owed_at_dates(
    liabilities: list[Account],
    ctx: BalanceContext,
    sample_dates: list[date],
    current_balances: dict[int, Decimal],
) -> dict[int, list[Decimal]]:
    """Return every liability's owed magnitude at each FORWARD sample date.

    The seam's multi-date, multi-account LIABILITY view.  It is KIND-BLIND: every
    liability's future reads the seam's one kind-correct multi-date producer,
    :func:`._kind_correct.balance_at_dates`, over the whole future sample axis --
    ONE read per liability per pass, never one per date -- and that producer, not
    this module, decides what each liability is:

    * **A configured loan** answers from the forward PLAN fold
      (:func:`~app.services.balance_at.positions`, step C6b): every date is
      strictly future here (filtered below), so it is seeded from the loan's
      confirmed balance and reduced by the payments it is PROJECTED to make --
      its projected transfer records at their live cash, then contractual
      synthesis beyond the record horizon.  The same forward balance the debt
      card and the ``2 years`` liability series read through the seam, so a band
      built on this cannot drift from them.
    * **Every other liability** -- a revolving Credit Card, a loan with no
      ``LoanParams``, a plain custom liability -- answers from its cash fold: its
      opening plus every recorded movement plus the still-projected plan
      (ruling R-G's clamp, the PLANNED tier), sampled at each future date.  A
      card with projected purchases and payments therefore MOVES across the
      horizon; one with no rows reads its opening flat, because that is what its
      fold says.  Until plan step credit_card:CC-1 this arm held every such
      liability FLAT at its current owed magnitude on the claim that it "has no
      forward model"; the fold IS its forward model (ruling R-CC14, design
      ``docs/design/credit_card_from_scratch.md`` 3.1).

    ``scenario`` is nullable, and this is the ONE public seam entry that does not
    call :func:`._inputs._require_scenario`.  That guard exists to turn a missing
    baseline into a loud failure instead of a silently wrong number; here a
    missing baseline has a correct answer of its own: with no baseline there is
    no loan to resolve AND no plan to fold, so every liability holds FLAT at its
    current owed magnitude.  Raising would force every caller to re-derive that
    flat hold, which is precisely the boundary-rule duplication the seam exists
    to prevent.  **This no-baseline hold is a gate of its own, older than CC-1
    and outside it**: whether the band should instead raise into ruling R-BW's
    one handler like every other seam entry is not a question this step
    answers.

    Sign convention: the result is a POSITIVE owed magnitude per date, matching
    the net-worth reduction's liability-minus rule (a liability contributes
    ``abs(bal)``, subtracted from the asset side -- see
    ``savings_dashboard_service._net_worth``).  *current_balances* may be
    signed either way (a loan resolves positive-owed, a Credit Card's cash
    balance is negative); ``abs`` is applied here.

    The today point comes from *current_balances*, NOT from a fresh read, and
    that is load-bearing: the caller's current balance is the ledger-confirmed
    figure the net-worth hero renders, so a band built on this reconciles with
    the hero at index 0 by construction.  A schedule walk at ``today`` would
    instead report a loan's balance net of any OVERDUE unconfirmed payment
    (understating the debt), which is why only STRICTLY-future dates are forwarded
    to :func:`._kind_correct.balance_at_dates`; ``today`` itself reads
    *current_balances* through the splice, never the projection.

    *today* is the CALLER'S as-of date, not a fresh :func:`datetime.date.today`
    read here, and that is deliberate.  The caller already built *sample_dates*
    against some notion of "now"; if this function re-read the clock, a request
    that crossed midnight between the two reads would see its own index-0 sample
    as a PAST date and raise -- turning a benign race into a 500 on a page that
    previously just held the band flat.  Re-reading would also silently assume
    the caller's dates are UTC-anchored, when the project's own policy is that
    user-facing dates are display-tz (``app/utils/dates``).  One clock, chosen by
    the caller, so the sample axis and the present/future boundary cannot
    disagree.  (The loan RESOLVER still takes its own as-of internally when it
    decides which payments are confirmed; that is unchanged and independent of
    this projection boundary.)

    Args:
        liabilities: The liability accounts to value (every one appears in the
            result).  ``account_type`` must be loaded -- the kind-correct
            producer classifies it through :func:`._resolution.configured_loan`
            to pick each liability's arm.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Its ``as_of`` is the present/future boundary AND the "now" its
            *sample_dates* were built against -- one clock, so this guard cannot
            reject the caller's own index-0 sample.  Its ``scenario`` may be
            ``None`` (no baseline: every liability holds flat -- see above); this
            is the one seam entry that tolerates that rather than raising.
        sample_dates: The calendar dates to value each liability at, in the
            desired output order (any order; the projection is joined BY DATE,
            not by position).  Every date must be on or after ``ctx.as_of``.
        current_balances: ``{account_id: Decimal}`` each liability's current
            balance as the caller already resolved it (the figure its hero
            renders).  A missing account is treated as ``0``.

    Returns:
        ``{account_id: [Decimal owed magnitude at each sample date]}`` -- one
        list per account in *liabilities*, aligned with *sample_dates*.

    Raises:
        ValueError: When any sample date precedes *today*.  A past balance is a
            LEDGER read, not a projection: ask
            :func:`~app.services.balance_at.balance_at`, which routes an
            amortizing account's past to the genesis ledger (the only complete
            record -- it books the true-ups that have no schedule row).
    """
    today = ctx.as_of
    stale = sorted({d for d in sample_dates if d < today})
    if stale:
        raise ValueError(
            "liability_owed_at_dates projects FORWARD; a past date is a ledger "
            "read, not a projection -- ask balance_at.balance_at, which reads "
            "the genesis ledger for an amortizing account's past. Rejected "
            f"dates: {[d.isoformat() for d in stale]} (today={today.isoformat()})"
        )

    # Deduplicated so a repeated sample date does not pay for a second fold
    # sample; the result is joined BY DATE below, so the producer's order and
    # cardinality are its own business, not an implicit contract.
    future_dates = sorted({d for d in sample_dates if d > today})
    forward_by_account: dict[int, dict[date, Decimal]] = {}
    if ctx.scenario is not None and future_dates:
        for account in liabilities:
            # ONE kind-correct read per liability over the whole future axis:
            # the producer picks the arm (a configured loan's positions, every
            # other liability's fold), so this module spells no kind test --
            # the configured-loan gate it carried until credit_card:CC-1 was
            # the seam's second copy of balance_at's dispatch.  It returns the
            # date-keyed dict the splice consumes directly.
            forward_by_account[account.id] = balance_at_dates(
                account, ctx, future_dates,
            )

    result: dict[int, list[Decimal]] = {}
    for account in liabilities:
        raw_current = current_balances.get(account.id)
        current = abs(raw_current) if raw_current is not None else ZERO
        forward = forward_by_account.get(account.id)
        if forward is None:
            # No baseline scenario (or no future dates to project): nothing can
            # be folded or resolved, so hold the current owed magnitude flat.
            result[account.id] = [current] * len(sample_dates)
            continue
        result[account.id] = _spliced_owed_series(
            sample_dates, today, current, forward,
        )
    return result
