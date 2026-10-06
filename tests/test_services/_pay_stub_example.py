"""The pay stub door's worked example, the one home of what its two service suites share.

``test_pay_stub_service.py`` and ``test_pay_stub_gross.py`` both price the
example below.  They were one module until plan step salary:S11-c-2c split the
printed-gross checks out of it (ledger row **SAL-594**: it had passed the
1,000-line limit ``app/`` modules are held under), and the figures, the
``world`` fixture and the helpers that build a stub from them moved here
rather than into both, so the two suites cannot drift apart.  Each suite
imports the names it uses; importing ``world_fixture`` registers the
``world`` fixture in that suite.

The worked example, computed by hand.  The seeded owner is paid every 14 days
from 2026-01-02 (``seed_periods``: ... 03-13, 03-27, 04-10 ...); the profile
earns ``$75,000.00`` a year, so base pay is ``75000 / 26 = 2884.615...`` ->
``$2,884.62``.  Its lines: Health Insurance ``$280.00`` and Vision ``$12.00``
(pre-tax, every paycheck), Dental ``$35.00`` (pre-tax, 12 a year: the first
paycheck of a month, so 03-13 and not 03-27), Roth IRA ``$100.00`` (post-tax)
and Phone Allowance ``$60.00`` (taxable earning).  The 03-27 stub prints
Health ``$280.00``, Dental ``$35.00``, Roth ``$110.00``, Phone ``$60.00`` and
no Vision; a one-off "Retro pay" ``$55.00`` (taxable earning); taxes
``$150.00`` / ``$100.00`` / ``$180.00`` / ``$42.00``.  So:

    gross     = 2884.62 + 60.00 + 55.00          = 2999.62
    pre-tax   = 280.00 + 35.00                   =  315.00
    taxes     = 150.00 + 100.00 + 180.00 + 42.00 =  472.00
    post-tax  = 110.00                           =  110.00
    net       = 2999.62 - 315.00 - 472.00 - 110.00 = 2102.62

The stub prints that gross and that net, the two totals the door checks.
"""

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PaycheckLineKindEnum, WithholdingKindEnum
from app.extensions import db
from app.services import pay_stub_service
from app.services.balance_at import BalanceContext
from app.services.pay_stub_service import (
    LineFigure,
    OneOffFigure,
    PrintedTotals,
    StubFigures,
)
from tests._test_helpers import build_pay_stub_world

TODAY = date(2026, 3, 20)
PAYDAY = date(2026, 3, 27)
NET = Decimal("2102.62")
GROSS = Decimal("2999.62")


def kind_id(member):
    """A paycheck-line kind's id."""
    return ref_cache.paycheck_line_kind_id(member)


def tax_id(member):
    """A withholding kind's id."""
    return ref_cache.withholding_kind_id(member)


@pytest.fixture(name="world")
def world_fixture(seed_user, seed_periods):  # pylint: disable=unused-argument
    """The worked example's profile and lines, committed.

    Pylint: ``unused-argument`` -- ``seed_periods`` is requested for the pay
    schedule it writes, which the Dental line's rule and every payday need.
    """
    profile, lines = build_pay_stub_world(seed_user)
    return {"profile": profile, "lines": lines, "user_id": seed_user["user"].id}


def fresh_ctx(world):
    """A fresh read pass for the world's owner."""
    return BalanceContext.build(world["user_id"])


def example_figures(world, *, payday=PAYDAY, roth="110.00", one_offs=None, base="2884.62"):
    """The worked example's figures, with the named departures.

    Each line is printed under its own kind; :func:`printed_under` moves one
    under another heading.
    """
    lines = world["lines"]

    def printed(key, amount):
        """The stub's figure for one line, under the line's own kind."""
        return LineFigure(lines[key].paycheck_line_kind_id, Decimal(amount))

    return StubFigures(
        payday=payday,
        base_pay=Decimal(base),
        line_amounts={
            lines["health"].id: printed("health", "280.00"),
            lines["dental"].id: printed("dental", "35.00"),
            lines["roth"].id: printed("roth", roth),
            lines["phone"].id: printed("phone", "60.00"),
        },
        withholdings={
            tax_id(WithholdingKindEnum.FEDERAL_INCOME): Decimal("150.00"),
            tax_id(WithholdingKindEnum.STATE_INCOME): Decimal("100.00"),
            tax_id(WithholdingKindEnum.SOCIAL_SECURITY): Decimal("180.00"),
            tax_id(WithholdingKindEnum.MEDICARE): Decimal("42.00"),
        },
        one_offs=(
            (OneOffFigure("Retro pay", kind_id(PaycheckLineKindEnum.TAXABLE_EARNING),
                          Decimal("55.00")),)
            if one_offs is None else one_offs
        ),
        notes=None,
    )


def printed_under(world: dict, figures: StubFigures, **kinds) -> StubFigures:
    """*figures* with each named line printed under another heading (ruling R-SAL58).

    Args:
        world: The worked example's world.
        figures: The stub's figures.
        **kinds: ``line key=PaycheckLineKindEnum`` for each line the stub
            prints under a heading other than the line's own.

    Returns:
        A copy of *figures*; each named line keeps its amount.
    """
    line_amounts = dict(figures.line_amounts)
    for key, member in kinds.items():
        line_id = world["lines"][key].id
        line_amounts[line_id] = LineFigure(kind_id(member), line_amounts[line_id].amount)
    return dataclasses.replace(figures, line_amounts=line_amounts)


def printed_totals(net=NET, gross=GROSS):
    """The totals the stub prints: the worked example's unless named."""
    return PrintedTotals(gross=gross, net=net)


def record_example(world, figures=None, printed_net=NET, printed_gross=GROSS):
    """Record *figures* (the worked example by default) and commit."""
    stub = pay_stub_service.record_stub(
        world["profile"], figures or example_figures(world),
        printed_totals(printed_net, printed_gross), fresh_ctx(world), TODAY,
    )
    db.session.commit()
    return stub
