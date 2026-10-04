"""Shekel Budget App -- the pay stub entry door's printed-GROSS check.

What a stub's figures must add up to against the gross it prints (ruling
**R-SAL99**, "Check the gross too", closing finding **SAL-590**), and how a
miss is worded.  One rule of :mod:`app.services.pay_stub_service`'s door,
held in its own module because that module reached pylint's 1000-line cap
(plan step salary:S11-c-2b): the door asks it from ``_refuse`` beside the
printed-net check, and nothing else does.

Flask-free and query-free: it takes the stub's derived totals and its
printed ones, and returns words.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.utils.money import ZERO

if TYPE_CHECKING:
    from app.services.pay_stub_service import PrintedTotals, StubTotals


def gross_refusal(
    base_pay: Decimal, totals: StubTotals, printed: PrintedTotals,
) -> str | None:
    """Return why the stub's figures miss its printed gross, or ``None`` when they make it.

    Ruling **R-SAL99**, "Check the gross too" (finding **SAL-590**), which
    reads the printed gross as base pay plus every taxable earning, a
    line's and a one-off's alike (:func:`totals_of`).

    **The "counted twice" wording reads TWO figures, and it is still a
    question.**  A gross typed into the Base pay box beside its taxable
    earnings, every other figure right, makes the base pay equal the printed
    gross AND puts the doubled amount into the net as well: the net misses
    by exactly what the gross does.  Neither figure is enough alone.  Base
    pay equal to the printed gross is not -- a stub with no taxable earning
    prints its base as its gross, and an after-tax earning entered as
    taxable then overshoots the gross while the net, which an earning joins
    either way, balances; blaming the Base pay box there sends the owner
    round between two refusals (an adversarial review of plan step
    salary:S11-c-2a measured it).  Nor is the net's matching miss -- a base
    pay typo moves both totals by the same amount.  The pair is what the
    slip makes when it is the only mistake, and it is not sufficient: a
    taxable line the stub does not print, entered on a stub with no taxable
    earning, makes every total the slip makes, which is why the wording ends
    in a question.  The slip beside a second mistake -- a deduction
    mistyped, a non-zero tax left out -- moves the net by more or less than
    the gross and is named only as a difference; the submit that corrects
    the second mistake names it.  Every other miss names the difference and
    where to look.

    A tax left out (the net's own refusal is then withheld) does not hold
    the wording back.  A ``$0.00`` tax left out makes every total the same
    as ``$0.00`` typed, so the wording is as right as it is with every tax
    typed; any other match needs misses that offset exactly -- such as a tax
    left out equal to an earning that moves the gross and not the net --
    which the next submit, with the tax typed, corrects.

    Args:
        base_pay: The base pay typed.
        totals: What the stub's figures add up to.
        printed: The gross and net the stub prints.

    Returns:
        The message to show on the printed gross, or ``None``.
    """
    excess = totals.gross - printed.gross
    if excess == ZERO:
        return None
    lines_make = (
        f"Base pay plus your taxable lines make ${totals.gross:,.2f}, but the stub "
        f"prints ${printed.gross:,.2f}"
    )
    if base_pay == printed.gross and totals.net - printed.net == excess:
        return (
            f"{lines_make}: ${excess:,.2f} is counted twice.  Is the gross in "
            f"the Base pay box?"
        )
    return (
        f"{lines_make} (a difference of ${abs(excess):,.2f}).  Check the base "
        f"pay, each taxable earning, and the kind each is entered under."
    )


__all__ = [
    "gross_refusal",
]
