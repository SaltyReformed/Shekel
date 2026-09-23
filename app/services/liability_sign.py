"""Shekel Budget App -- the liability SIGN: what a balance owes, and what a door speaks.

**Every balance is what the account HOLDS, negative when owed** (ruling
**R-CC47**): a card owing ``$1,000.00`` holds ``-1,000.00``, and one holding a
``$50.00`` credit holds ``+50.00``.  Two questions sit on top of that one sign,
and this module answers both:

* **What does it owe?**  :func:`owed`, minus the balance -- ruling R-CC29's one
  flip.
* **What does a balance DOOR speak?**  A door that takes a liability's balance
  asks for the amount OWED and stores the held sign (ruling **R-CC52**), on
  every surface it opens from (ruling **R-CC57**); a door on any other account
  speaks the balance itself.  :func:`entered_figure` is the figure a door shows
  or pre-fills for a held balance, and :func:`held_balance` is the held balance
  it stores for a figure typed into it -- the same crossing, because the flip
  is its own inverse.

**Why a module of its own** (plan step credit_card:CC-5-5b).  The flip lived in
:mod:`app.services.card_statement` (R-CC29), then in the balance seam's
``_liability`` module (plan step CC-5-5a, R-CC47).  It left the seam when the
DOORS became its callers, for two reasons.  The rule a door needs -- is this
account a liability? -- is account metadata, which
:mod:`app.services.account_category` owns and the seam deliberately does not.
And the seam's own configured-loan arms will read the flip at plan step
CC-5-5c while ``_liability`` imports those arms, so a flip living there would be
a circular import.  This module imports :mod:`app.services.account_category`
and nothing of the seam, so every layer can reach it.

Boundary discipline (``CLAUDE.md``): no Flask import, no database access.  All
money is :class:`~decimal.Decimal`.
"""

from decimal import Decimal

from app.services.account_category import is_liability_type


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
    credit_card:CC-5-5a moved it into the balance seam under ruling **R-CC47**
    ("'Owed' is minus the balance, R-CC29's one flip moved into the balance
    seam"); plan step CC-5-5b moved it here (see the module docstring).  A
    statement producer will state its figures as ``owed(cash_balance_at(...))``
    (none is wired in ``app/`` yet).  Today's readers are the savings cockpit's
    revolving-debt footer
    (:func:`app.services.savings_dashboard_service._debt_line.debt_without_payoff_model`,
    ruling R-CC49) and the balance doors, through :func:`entered_figure`.

    **Which balances are HELD today, precisely** -- in the seam's arithmetic.
    The kind-correct seam's for every account that is NOT a configured loan (a
    Credit Card, a loan with no ``LoanParams``, a custom liability, every
    asset), and the cash fold's for every account that is NOT a configured loan.
    Two exclusions, both measured on the production-shape clone at CC-5-5a:

    * A CONFIGURED loan's kind-correct balance
      (:func:`~app.services.balance_at.balance_at` and the period maps) is
      reported as an OWED figure -- the Mortgage's ``176,719.77`` -- which is why
      :func:`~app.services.balance_at.liability_owed_at_dates` and the net-worth
      hero still take ``abs`` rather than this.  Plan step credit_card:CC-5-5c
      re-signs that arm to the held sign (R-CC47) and moves those readers onto
      this flip.
    * A configured loan's CASH fold is not a balance of the loan in EITHER
      sign, and never becomes one: it is the account-level assertion as typed
      plus each whole payment INTO the loan (interest and escrow included),
      with no interest accrued -- the same Mortgage's ``cash_balance_at`` reads
      ``+185,747.21``, and the Van Loan's ``2,127.76`` is its ``0.00``
      assertion plus four ``531.94`` payments.  Plan step CC-5-5c re-signs the
      kind-correct arm only, so this stays true after it: a configured loan's
      cash fold is NEVER an input to this function.

    Until CC-5-5c a caller must not hand this the first figure, and it must
    never hand it the second.

    **What an owner TYPED is held by construction since plan step CC-5-5b**
    (ruling R-CC52): every door that takes a liability's balance stores
    :func:`held_balance` of the figure typed.  Rows typed BEFORE that step
    carry whatever sign was entered, and two different things keep them out
    of this function -- one structural, one only measured:

    * **Structural, for a CONFIGURED loan.**  Every door crossing through
      :func:`entered_figure` refuses or skips an AMORTIZING account (the
      anchor editor, its save and its preview refuse one; the books-opening
      card is not built for one and its POST refuses one), except the loan
      setup page, which renders only while a loan has NO terms; and the
      loan's read-only anchor cell reads no assertion at all (ruling
      **R-CC53**).  So the production Mortgage's owed-typed opening
      (``+174,281.51``) and assertion (``+178,103.41``) reach no crossing.
    * **Measured only, for everything else.**  A non-amortizing liability or
      a loan still without terms, typed in the owed sign before this step,
      WOULD be an input: a legacy terms-less Auto Loan asserted
      ``+5,000.00`` would open its setup page on ``-5000.00``, which that
      box's ``min="0"`` refuses.  The census of the 2026-09-22 17:06
      production dump found none -- 9 accounts, 2 liabilities, both
      configured loans; no card, no custom type, no loan without terms --
      and that census, not any construction, is the premise.

    Args:
        balance: A HELD balance (see above for which balances are).

    Returns:
        ``-balance``: positive when the owner owes, negative when the account
        holds a credit, and an unsigned ``0.00`` at zero (``decimal`` negates a
        zero to a positive zero outside ``ROUND_FLOOR``, which nothing here
        sets).
    """
    return -balance


def asks_owed(account_type) -> bool:
    """Return whether a balance DOOR on this account type asks for the amount OWED.

    The one decision every door's words and every crossing read: the "Amount
    owed" labels (the anchor editor, the books-opening card, the create form's
    option marks), the stale-form guard (ruling **R-CC61**) and
    :func:`entered_figure` itself -- so a door cannot label a box owed and
    cross it as a balance, or the reverse.  A liability asks owed (ruling
    **R-CC52**); every other account asks its balance.

    Args:
        account_type: The account's :class:`~app.models.ref.AccountType`.

    Returns:
        ``True`` for a liability type.
    """
    return is_liability_type(account_type)


def entered_figure(account_type, balance: Decimal) -> Decimal:
    """Return the figure a balance DOOR shows for a HELD *balance*.

    For a LIABILITY it is the amount OWED (ruling **R-CC52**: "every door that
    takes a balance for a LIABILITY account ... asks for the amount OWED"), so a
    card holding ``-1,000.00`` pre-fills ``1,000.00`` and one holding a
    ``+50.00`` credit pre-fills ``-50.00``.  For every other account it is the
    balance unchanged, so an asset's door reads exactly what it did before
    CC-5-5b.

    **The doors, and only the doors.**  Every PRE-FILL, and every echo of a
    STORED figure, reads this: the anchor editor on each of the surfaces it
    opens from, its difference preview and its acknowledgement; the
    books-opening card; the loan setup page's "Balance today".  An echo of a
    figure just TYPED -- the editor's rejection redisplay, the restatement's
    flash messages -- is shown back as typed, uncrossed, because it is already
    in the door's words.  The surfaces the editor opens FROM keep their own
    sign (ruling **R-CC57**): a card's grid, cash page and dashboard show the
    held balance its rows are summed in, and only the figure a door asks for
    speaks owed.

    Args:
        account_type: The account's :class:`~app.models.ref.AccountType` --
            the type, not the account, because the create form classifies a
            balance before its account exists.
        balance: A HELD balance.

    Returns:
        :func:`owed` of *balance* where the door :func:`asks_owed`, else
        *balance*.
    """
    if asks_owed(account_type):
        return owed(balance)
    return balance


def held_balance(account_type, entered: Decimal) -> Decimal:
    """Return the HELD balance a door stores for the figure *entered* into it.

    The inverse of :func:`entered_figure`, and the same crossing: the flip is
    its own inverse, so this is :func:`entered_figure` rather than a second
    spelling of it.  Typing ``5,000.00`` on a car loan with no terms stores
    ``-5,000.00``, which reads ``$5,000.00`` owed (R-CC52's own worked case);
    typing ``-50.00`` on a card stores a ``+50.00`` credit.

    Each door crosses ONCE, at its route, before the service: the services keep
    taking the held balance they always took, so every non-form caller of
    :func:`app.services.account_service.create_account`,
    :func:`app.services.anchor_service.apply_anchor_true_up` and
    :func:`app.services.opening_service.apply_opening_restatement` is unchanged.

    Args:
        account_type: The account's :class:`~app.models.ref.AccountType`.
        entered: The figure the owner typed into a balance door.

    Returns:
        The held balance to store.
    """
    return entered_figure(account_type, entered)
