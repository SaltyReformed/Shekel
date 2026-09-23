"""
Shekel Budget App -- a balance form refused when its meaning went stale

**Ruling R-CC61** (plan step credit_card:CC-5-5b).  A liability's balance box
asks for the amount OWED and every other account's asks for its balance
(:func:`app.services.liability_sign.asks_owed`), and an account's kind is
EDITABLE: an account with no postings may be re-typed across asset and
liability in another tab while one of its balance forms is open (finding
N-199's race, one field over), and a custom type's category may be edited in
place.  Read at SAVE, the typed figure would be crossed under a meaning the box
never showed: a $0.00 Checking editor re-typed to a Credit Card, saved at
2,500.00, stored a card owing $2,500.00 where the base code stored a $2,500.00
balance -- $5,000.00 apart (measured by CC-5-5b's adversarial review).

So each form states what its box asked (``asks_owed``, which the anchor and
restatement schemas read; absent means "the balance"), and three doors refuse a
mismatch before anything is staged: the anchor editor's save, which re-opens the
editor as a fresh click would (ruling **R-CC62**); the books-opening POST, whose
refusal returns to the edit page and its standing figures; and the difference
preview, which says the save would be refused instead of pricing the figure.
The create form is R-CC61's stated exception -- its type is picked in the same
submission (see :func:`app.routes.accounts.crud.create_account`).

**What "refused before anything is staged" covers, precisely.**  The account's
type is read when the route runs, before the write door takes the owner's write
lock; a re-type that commits in the sub-second between the two is not seen.
The amortizing-kind refusal (N-199) has had the same window all along.  The
guard closes the race a person can produce with two tabs, not a scheduler's.

Its own module because three route modules share it and ``anchor`` sits near
the 1000-line module ceiling, where findings N-152 / N-156 / N-201 rule a split
rather than shaved prose.
"""

from app.models.account import Account
from app.services import liability_sign


def _meaning_changed(account: Account, asked_owed: bool) -> bool:
    """Return whether *account*'s box now asks something other than *asked_owed*.

    Args:
        account: The owned, attached :class:`Account`.
        asked_owed: What the submitted form says its box asked -- ``True`` for
            the amount owed; a form that states nothing asked for the balance.

    Returns:
        ``True`` when the account's meaning differs from the form's.
    """
    return asked_owed != liability_sign.asks_owed(account.account_type)


def door_meaning_refusal(account: Account, asked_owed: bool) -> str | None:
    """Return why a SAVE of a form rendered under the other meaning is refused.

    The sentence ruling R-CC61 picked.  It names the account's type as the
    cause, which is the ordinary case; a page rendered before CC-5-5b's deploy
    (whose card editor asked the held balance and states nothing) and a custom
    type whose category was edited reach it too, and are refused as they
    should be.

    Args:
        account: The owned, attached :class:`Account` being saved.
        asked_owed: What the submitted form says its box asked.

    Returns:
        The refusal to show beside the re-opened form, or ``None`` when the
        form's meaning is the account's.
    """
    if not _meaning_changed(account, asked_owed):
        return None
    asks_for = (
        "the amount owed"
        if liability_sign.asks_owed(account.account_type)
        else "the account's balance"
    )
    return (
        "This account's type changed while you were editing; the box now asks "
        f"for {asks_for}. Check the figure and save again."
    )


def door_meaning_preview_refusal(account: Account, asked_owed: bool) -> str | None:
    """Return what the difference PREVIEW says for a stale form, or ``None``.

    Its own sentence rather than the save's: the preview refuses nothing and
    re-opens nothing, and the box on screen still carries its old words, so
    "check the figure and save again" would be wrong under it (CC-5-5b's
    re-review, finding L-d).

    Args:
        account: The owned, attached :class:`Account` being previewed.
        asked_owed: What the form's region says its box asked.

    Returns:
        The caption, or ``None`` when the form's meaning is the account's.
    """
    if not _meaning_changed(account, asked_owed):
        return None
    return (
        "This account's type changed while you were editing, so saving this "
        "box would be refused. Open the balance again to see what it now "
        "asks for."
    )
