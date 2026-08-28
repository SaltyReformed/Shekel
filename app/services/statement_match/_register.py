"""Everything the REGISTER shows: the answers given, and the acts accepted.

Plan step ``bank_import:X-gf-2``, ruling **bank_import:R-GX**.  The mirror of
:func:`~._reads.review_set` on the other half of the split: that one assembles
what is still being DECIDED, and this assembles what has been.  ONE assembly
for the same reason that one is one -- a screen whose two cards came from two
callers' own sequencing would put the composition in the route, where this
project's layering says it may not live.

**It takes an owner and an account, not a**
:class:`~._scope.ReviewScope`, and that is the whole cost difference between
the two screens.  A stated answer is one table read (``merchant_rules`` joined
to ``merchants``) and an accepted act is another; neither needs the pay
calendar, the candidate derivation or the matcher, all of which the review pass
pays for.  Measured on the developer's own data 2026-08-27: these two cards
were 442,109 bytes of a 578,523-byte review body, and rendering them cost that
screen a valuation of all 221 of his accepted acts.

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._accepted_view import REGISTER_LIMIT, AcceptedRegister, accepted_register
from ._bars import CreationBars
from ._rules import RuleView
from ._section import MerchantRegister, answered_merchants


@dataclass(frozen=True)
class StatementRegister:
    """What this account has already decided, in one value.

    Attributes:
        merchants: Every merchant answer the owner has given
            (:class:`~._section.MerchantRegister`), restatable here and never
            withdrawable (ruling **R-GS**).
        accepted: The acts already accepted
            (:class:`~._accepted_view.AcceptedRegister`) -- every one that no
            longer holds first, then the newest of the rest.
    """

    merchants: MerchantRegister
    accepted: AcceptedRegister


def register_set(
    owner_id: int, account_id: int, limit: int | None = REGISTER_LIMIT,
) -> StatementRegister:
    """Return everything the register screen shows for one account.

    **One derivation at one instant**, which is the argument
    :class:`~._rules.RuleView` makes one tier down: the answers and what they
    can still resolve against are read from the same moment, so the control
    cannot offer an option the door would refuse.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account.
        limit: How many SETTLED acts to render, or ``None`` for the whole
            record.  See :data:`~._accepted_view.REGISTER_LIMIT` for why the
            bound falls on the settled remainder alone.

    Returns:
        Its :class:`StatementRegister`.  A caller that has just written an
        ANSWER wants :func:`merchant_register` and its own existing
        ``accepted`` instead: only one half can have moved.
    """
    return StatementRegister(
        merchants=merchant_register(owner_id, account_id),
        accepted=accepted_register(owner_id, account_id, limit),
    )


def merchant_register(owner_id: int, account_id: int) -> MerchantRegister:
    """Return the ANSWERS half of the register, on its own.

    **The half a rule pass changes, separable from the half it cannot**
    (adversarial review 2026-08-27).  ``state_rules`` writes exactly
    ``budget.merchant_rules`` -- no transaction, no purchase, no act -- so the
    accepted list is provably the same before and after it, and a door that
    re-derived the WHOLE register for its answer would re-fold every act on the
    account to show a changed sentence.  That fold is what this step took off
    the review screen; re-paying it one door over would be the same cost with a
    different caller.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account.

    Returns:
        The :class:`~._section.MerchantRegister` -- three small indexed reads,
        against the act fold beside it.
    """
    view = RuleView.build(owner_id, account_id)
    # The bars are read even here, because this is where an answer is CHANGED:
    # a merchant a source files as a payment to an account the owner holds has
    # two of its four options refused by the door (ruling **R-GJ**), and a
    # control that did not say so would be the *chooser whose submission can
    # never succeed* shape this package has closed four times.
    return answered_merchants(
        view, CreationBars.build(owner_id, account_id, view.rules),
    )
