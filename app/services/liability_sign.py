"""Shekel Budget App -- the liability SIGN: what a balance owes, and what a door speaks.

**Every balance is what the account HOLDS, negative when owed** (ruling
**R-CC47**): a card owing ``$1,000.00`` holds ``-1,000.00``, and one holding a
``$50.00`` credit holds ``+50.00``.  Two questions sit on top of that one sign,
and this module answers both:

* **What does it owe?**  :func:`owed`, minus the balance -- ruling R-CC29's one
  flip.
* **What does an owed-speaking surface show?**  A door that takes a
  liability's balance asks for the amount OWED and stores the held sign
  (ruling **R-CC52**), on every surface it opens from (ruling **R-CC57**), and
  the /savings cockpit shows every liability figure as owed (ruling R-CC47:
  "every loan screen and liability tile shows what is owed").  On any other
  account both speak the balance itself.  :func:`shown_figure` is the figure
  such a surface shows or pre-fills for a held balance, and
  :func:`held_balance` is the held balance a door stores for a figure typed
  into it -- the same crossing, because the flip is its own inverse.

**Why a module of its own** (plan step credit_card:CC-5-5b).  The flip lived in
:mod:`app.services.card_statement` (R-CC29), then in the balance seam's
``_liability`` module (plan step CC-5-5a, R-CC47).  It left the seam when the
DOORS became its callers, for two reasons.  The rule a door needs -- is this
account a liability? -- is account metadata, which
:mod:`app.services.account_category` owns and the seam deliberately does not.
And the seam's own configured-loan arms read the flip (plan step CC-5-5c)
while ``_liability`` imports those arms, so a flip living there would be a
circular import.  This module imports :mod:`app.services.account_category`
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
    (none is wired in ``app/`` yet).  Since plan step CC-5-5c it is every
    owed figure the app shows: the seam's two configured-loan arms report the
    held sign through it (:func:`app.services.balance_at.balance_at_dates`'s
    loan arm and the per-period map's), and the liability band, the net-worth
    hero, the debt summary, /debt-strategy, the loan pages, home equity and
    the archived drawer read what a debt owes through it -- directly, or through
    :func:`shown_figure`.

    **Every balance the seam reports is HELD since plan step CC-5-5c** --
    the kind-correct seam's for every account, a configured loan included
    (its arms turn the loan domain's owed ``positions()`` into the held sign
    here, R-CC47), and the cash fold's for every account that is NOT a
    configured loan.  One exclusion remains, measured on the production-shape
    clone at CC-5-5a: a configured loan's CASH fold is not a balance of the
    loan in EITHER sign, and never becomes one.  It is the account-level
    assertion as typed plus each whole payment INTO the loan (interest and
    escrow included), with no interest accrued -- the Mortgage's
    ``cash_balance_at`` read ``+185,747.21`` where it owed ``176,719.77``, and
    the Van Loan's ``2,127.76`` is its ``0.00`` assertion plus four ``531.94``
    payments.  A caller must never hand this function that figure.

    **What an owner TYPED is held by construction since plan step CC-5-5b**
    (ruling R-CC52): every door that takes a liability's balance stores
    :func:`held_balance` of the figure typed.  Rows typed BEFORE that step
    carry whatever sign was entered, and two different things keep them out
    of this function -- one structural, one only measured:

    * **Structural, for a CONFIGURED loan.**  Every door that crosses a
      STORED figure through :func:`shown_figure` refuses or skips an
      AMORTIZING account (the anchor editor and its preview refuse one; the
      books-opening card is not built for one), except the loan setup page,
      which renders only while a loan has NO terms; the loan's read-only
      anchor cell reads no assertion at all (ruling **R-CC53**); and the
      archived drawer reads a debt's seam balance, never its assertion
      (ruling **R-CC67**).  (The create door does cross a new loan's figure,
      but a TYPED one, never a stored row.)  So the production Mortgage's
      owed-typed opening (``+174,281.51``) and assertion (``+178,103.41``)
      reach no crossing.
    * **Measured only, for everything else.**  A non-amortizing liability or
      a loan still without terms, typed in the owed sign before CC-5-5b,
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
    :func:`shown_figure` itself -- so a door cannot label a box owed and cross
    it as a balance, or the reverse, and the /savings cockpit cannot show a
    figure in words its editor does not ask in.  A liability asks owed
    (ruling **R-CC52**); every other account asks its balance.

    Args:
        account_type: The account's :class:`~app.models.ref.AccountType`.

    Returns:
        ``True`` for a liability type.
    """
    return is_liability_type(account_type)


def shown_figure(account_type, balance: Decimal) -> Decimal:
    """Return the figure an owed-speaking surface shows for a HELD *balance*.

    For a LIABILITY it is the amount OWED (ruling **R-CC52**: "every door that
    takes a balance for a LIABILITY account ... asks for the amount OWED";
    ruling R-CC47: "every loan screen and liability tile shows what is owed"),
    so a card holding ``-1,000.00`` shows ``1,000.00`` and one holding a
    ``+50.00`` credit shows ``-50.00``.  For every other account it is the
    balance unchanged.

    **Which surfaces speak owed.**  The balance DOORS: every pre-fill and
    every echo of a STORED figure -- the anchor editor on each of the surfaces
    it opens from, its difference preview and its acknowledgement; the
    books-opening card; the loan setup page's "Balance today".  An echo of a
    figure just TYPED -- the editor's rejection redisplay, the restatement's
    flash messages -- is shown back as typed, uncrossed, because it is already
    in the door's words.  And the /savings cockpit (plan step CC-5-5c): each
    tile's figure, its projected caption and its sparkline, and the group
    subtotal, read through
    :attr:`~app.services.savings_dashboard_service._types.AccountProjection.shown_balance`.
    The surfaces a door opens FROM elsewhere keep their own sign (ruling
    **R-CC57**): a card's grid, cash page and dashboard show the held balance
    its rows are summed in.

    It was ``entered_figure`` until plan step CC-5-5c, named for the doors
    that were its only callers; the cockpit's figure is the same crossing, so
    it is renamed rather than spelled twice.

    Args:
        account_type: The account's :class:`~app.models.ref.AccountType` --
            the type, not the account, because the create form classifies a
            balance before its account exists.
        balance: A HELD balance.

    Returns:
        :func:`owed` of *balance* where the account :func:`asks_owed`, else
        *balance*.
    """
    if asks_owed(account_type):
        return owed(balance)
    return balance


def held_balance(account_type, entered: Decimal) -> Decimal:
    """Return the HELD balance a door stores for the figure *entered* into it.

    The inverse of :func:`shown_figure`, and the same crossing: the flip is
    its own inverse, so this is :func:`shown_figure` rather than a second
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
    return shown_figure(account_type, entered)
