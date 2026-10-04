"""Shekel Budget App -- the pay stub entry door's printed-GROSS check.

What a stub's figures must add up to against the gross it prints (ruling
**R-SAL99**, "Check the gross too", closing finding **SAL-590**; on a job
whose stub's gross also holds the after-tax earnings, ruling **R-SAL102**,
closing **SAL-592**), and how a miss is worded (ruling **R-SAL106**, one rule
read off the net, amending R-SAL104; R-SAL108, R-SAL110 and R-SAL111 name
causes it had left out).  One rule of
:mod:`app.services.pay_stub_service`'s door, held in its own module because
that module reached pylint's 1000-line cap (plan step salary:S11-c-2b): the
door asks :func:`gross_refusals` from ``_refuse`` beside the printed-net
check, and the entry form's route reads :func:`gross_counts` for the line
under the gross box, so the two say what the check counts in one spelling.

Flask-free and query-free: it takes the stub's derived totals, its printed
ones and the job's yes/no, and returns words.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.utils.money import ZERO

if TYPE_CHECKING:
    from app.services.pay_stub_service import PrintedTotals, StubTotals


def gross_counts(includes_after_tax: bool) -> str:
    """Word which earnings the printed-gross check adds to base pay.

    ``"taxable"`` (ruling **R-SAL99**), or ``"taxable and after-tax"`` on a job
    whose stub's gross also holds its after-tax earnings (ruling **R-SAL102**).
    The one spelling the check's refusals and the entry form's line under the
    gross box both read.

    Args:
        includes_after_tax: The job's ``stub_gross_includes_after_tax``.

    Returns:
        The phrase, to stand before "lines" or "earning".
    """
    return "taxable and after-tax" if includes_after_tax else "taxable"


#: The refusal key that names the job's setting beside a printed-gross refusal
#: (rulings R-SAL104 and R-SAL106): the setting's own field name, which the
#: entry form renders with a link to it, because this module cannot build one.
SETTING_KEY = "stub_gross_includes_after_tax"


def gross_refusals(
    base_pay: Decimal, totals: StubTotals, printed: PrintedTotals,
    *, includes_after_tax: bool,
) -> dict[str, str]:
    """Return why the stub's figures miss its printed gross: empty when they make it.

    Ruling **R-SAL99**, "Check the gross too" (finding **SAL-590**), reads the
    printed gross as base pay plus every taxable earning, a line's and a
    one-off's alike (:func:`~app.services.pay_stub_service.totals_of`).  On a
    job whose stub's gross also holds the after-tax earnings (the profile's
    ``stub_gross_includes_after_tax``, ruling **R-SAL102**, closing finding
    **SAL-592**) the check adds those too.  **What "yes" costs, which the
    ruling accepted:** on such a job the gross cannot tell a taxable earning
    from an after-tax one, and the net never could (an earning joins it
    either way), so an earning entered under the wrong one of the two passes
    both checks.  Only the saved stub's page shows it: a line's kind beside
    its paycheck line's, flagged when they differ, and a one-off's kind as
    entered, with nothing to compare it against.

    **How a miss is worded: ONE rule, read off the net** (ruling
    **R-SAL106**, "One rule by the net", which amends R-SAL99's wording and
    R-SAL104's trigger; **R-SAL108** and **R-SAL110** name the typed gross
    wherever the net is exact, and **R-SAL111** the earning the stub does not
    print beside the Base pay box).  The door checks two printed totals, and
    each single typing mistake moves them as a pair -- the gross miss (what
    the checked lines make less the printed gross) and the net miss (the
    lines' net less the printed net).  A refusal names the single-mistake
    causes its pair allows:

    * **net exact** -- if one mistake is all there is, every amount is
      right, so the gross is off in what it holds or in how it was typed,
      and the typed gross is named every time (R-SAL108, R-SAL110: a typo in
      the gross box makes this pair at any amount).  On a "no" job short by
      exactly the after-tax total, R-SAL104's question beside it (an earning
      the stub taxes entered as after-tax) and the setting (a gross that
      holds the after-tax earnings: the job should be "yes"); on a "yes" job
      over by exactly that total, the setting (a gross that leaves them out:
      on a "yes" job no heading moves the gross); otherwise, on a "no" job,
      which earnings are taxable and which after-tax;
    * **net off by the same** -- an amount the gross counts is off: base pay,
      an earning's amount, an earning left out or added.  When base pay
      equals the printed gross -- the gross can then only be over -- the two
      single mistakes that make that pair are both asked: the gross typed
      into the Base pay box (R-SAL99's slip) and an earning of a kind the
      check counts, entered on a stub that prints none (R-SAL111);
    * **net off by twice** -- one amount on the wrong side: a deduction
      entered as an earning (gross over) or an earning entered as a
      deduction (gross short);
    * **anything else** -- no single mistake makes it: the difference alone.

    **A pair can belong to more than one cause, and two mistakes can make a
    single mistake's pair.**  Where one pair has several single-mistake
    causes, the wording asks after each and the owner tells which.  The
    wordings that state -- "so the amounts are right" (R-SAL108), "so an
    amount is wrong" (R-SAL106) -- are the rulings' own words and are wrong
    when two mistakes offset into the pair (base pay and a tax off by the
    same amount read as net exact; both printed totals typed off by the
    same read as an amount), and so is every cause the either/or of a net
    exact miss of the after-tax total names: base pay and a tax both off by
    exactly that total make it, and taking the setting's exit then saves
    both mistakes.  The submit with one of them fixed reads true.
    **A tax left out** is no gross figure but shifts the net miss by its
    amount: ``$0.00`` left out changes nothing, so every wording is as right
    as with every tax typed; a non-zero one moves the pair, which then reads
    as "anything else" unless the shift happens to match, and the submit
    with the tax typed reads true.

    Args:
        base_pay: The base pay typed.
        totals: What the stub's figures add up to.
        printed: The gross and net the stub prints.
        includes_after_tax: The job's ``stub_gross_includes_after_tax``.

    Returns:
        ``{"printed_gross": message}``, with a :data:`SETTING_KEY` message
        beside it when the wording names the setting; empty when the
        figures make the printed gross.
    """
    checked = totals.gross + (totals.after_tax if includes_after_tax else ZERO)
    gross_miss = checked - printed.gross
    if gross_miss == ZERO:
        return {}
    lines_make = (
        f"Base pay plus your {gross_counts(includes_after_tax)} lines make "
        f"${checked:,.2f}, but the stub prints ${printed.gross:,.2f}"
    )
    net_miss = totals.net - printed.net
    if net_miss == ZERO:
        return _net_exact(lines_make, gross_miss, totals.after_tax, includes_after_tax)
    return {"printed_gross": _amount_miss(
        lines_make, gross_miss, net_miss, gross_is_base=base_pay == printed.gross,
    )}


def _net_exact(
    lines_make: str, gross_miss: Decimal, after_tax: Decimal, includes_after_tax: bool,
) -> dict[str, str]:
    """Word a gross miss beside an exact net: how the gross was typed, or what it holds.

    The first bullet of :func:`gross_refusals`' rule (rulings R-SAL104,
    R-SAL106, R-SAL108, R-SAL110).
    """
    if not includes_after_tax and gross_miss == -after_tax:
        return {
            "printed_gross": (
                f"{lines_make}: ${-gross_miss:,.2f} short, the same as your after-tax "
                f"earnings.  Check the gross you typed.  Is one of them taxed on your "
                f"stub?"
            ),
            SETTING_KEY: (
                "If your stub's gross includes after-tax earnings, set that on your "
                "salary profile."
            ),
        }
    if includes_after_tax and gross_miss == after_tax:
        return {
            "printed_gross": (
                f"{lines_make}: ${gross_miss:,.2f} over, the same as your after-tax "
                f"earnings, and your figures make the stub's net: check the gross you "
                f"typed, or your stub's gross leaves after-tax earnings out."
            ),
            SETTING_KEY: "Set that on your salary profile.",
        }
    headings = "" if includes_after_tax else (
        ", and which earnings are taxable and which after-tax"
    )
    return {"printed_gross": (
        f"{lines_make} (a difference of ${abs(gross_miss):,.2f}).  Your figures make "
        f"the stub's net, so the amounts are right: check the gross you typed{headings}."
    )}


def _amount_miss(
    lines_make: str, gross_miss: Decimal, net_miss: Decimal, *, gross_is_base: bool,
) -> str:
    """Word a gross miss beside a net miss: an amount off, on the wrong side, or several.

    The last three bullets of :func:`gross_refusals`' rule (rulings R-SAL99,
    R-SAL106, R-SAL111).
    """
    if net_miss == gross_miss:
        if gross_is_base:
            return (
                f"{lines_make}: ${gross_miss:,.2f} over, and the net is off by the "
                f"same.  Is the gross in the Base pay box, or is an earning entered "
                f"that the stub does not print?"
            )
        return (
            f"{lines_make} (a difference of ${abs(gross_miss):,.2f}).  The net is off "
            f"by the same, so an amount is wrong: check the base pay and each "
            f"earning's amount."
        )
    if net_miss == 2 * gross_miss:
        if gross_miss > ZERO:
            return (
                f"{lines_make}: ${gross_miss:,.2f} over; the net is off by twice "
                f"that.  Is a deduction entered as an earning?  Check each line's kind."
            )
        return (
            f"{lines_make}: ${-gross_miss:,.2f} short; the net is off by twice that.  "
            f"Is an earning entered as a deduction?  Check each line's kind."
        )
    return (
        f"{lines_make} (a difference of ${abs(gross_miss):,.2f}).  Check each figure "
        f"against the stub."
    )

__all__ = [
    "SETTING_KEY",
    "gross_counts",
    "gross_refusals",
]
