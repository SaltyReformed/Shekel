"""
Shekel Budget App -- the pay stub entry door's service (plan step salary:S11-b).

What :mod:`app.services.pay_stub_service` decides, graded without a request:

* which dates can carry a stub -- a payday the app holds or projects, up to
  the owner's next one (rulings **R-SAL48**, **R-SAL49**);
* what a stub's figures add up to, through the one waterfall a priced
  paycheck's net uses, and the printed-net check against it (**R-SAL42**);
* the printed-GROSS check (**R-SAL99**, finding **SAL-590**, which reads the
  printed gross as base pay plus the taxable earnings): a gross typed as base
  pay is named as counting every taxable earning twice, and an earning moved
  between taxable and after-tax -- which moves the gross but not the net -- is
  refused on the gross, in both directions;
* the job's answer to "my stub's gross includes after-tax earnings"
  (**R-SAL102**, finding SAL-592): "yes" adds them to the checked gross, and
  cannot see an earning under the other of the two headings (the ruling's
  accepted cost);
* how a gross miss is worded, read off the net's miss beside it (**R-SAL106**,
  amending R-SAL104; **R-SAL108**, **R-SAL110**, **R-SAL111**): net exact
  names the typed gross, and beside it R-SAL104's question and the setting
  on a "no" job short by exactly the after-tax total, the setting on a "yes"
  job over by it, else the headings on a "no" job; net off by the same names
  an amount, or -- when base pay is the printed gross -- the Base pay box or
  an earning the stub does not print; twice names a line on the wrong side;
  anything else, the difference;
* the one-off name clash, ignoring capitals and extra spaces (**R-SAL45**,
  **R-SAL51** (b)), asked only of a one-off a save adds or renames
  (**R-SAL57**);
* that a stub line adds up by the kind the STUB prints it under, a re-kind of
  its paycheck line moving no saved stub (**R-SAL58**, finding SAL-567);
* that a record on a held payday and an edit onto one are both refused
  (**R-SAL52**), every tax is required, and an edit writes the stub ROW once
  -- so its version guards a child-only change -- while an identical
  re-submit writes nothing;
* the comparison report: each line beside the app's figure that payday, and
  the base-pay gap (forks 2 and 5);
* the paycheck-line delete refusal's wording (**R-SAL51** (c)).

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
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import PaycheckLineKindEnum, WithholdingKindEnum
from app.exceptions import NotFoundError, PayStubRefused
from app.extensions import db
from app.models.pay_stub import PayStub, PayStubLineAmount, PayStubOneOff
from app.services import pay_stub_service
from app.services.balance_at import BalanceContext
from app.services.pay_stub_service import (
    LineFigure,
    OneOffFigure,
    PrintedTotals,
    StubFigures,
)
from tests._test_helpers import (
    build_pay_stub_world,
    make_flat_paycheck_line,
    make_salary_profile,
)

_TODAY = date(2026, 3, 20)
_PAYDAY = date(2026, 3, 27)
_NET = Decimal("2102.62")
_GROSS = Decimal("2999.62")


def _kind(member):
    """A paycheck-line kind's id."""
    return ref_cache.paycheck_line_kind_id(member)


def _tax(member):
    """A withholding kind's id."""
    return ref_cache.withholding_kind_id(member)


@pytest.fixture(name="world")
def _world(seed_user, seed_periods):  # pylint: disable=unused-argument
    """The worked example's profile and lines, committed.

    Pylint: ``unused-argument`` -- ``seed_periods`` is requested for the pay
    schedule it writes, which the Dental line's rule and every payday need.
    """
    profile, lines = build_pay_stub_world(seed_user)
    return {"profile": profile, "lines": lines, "user_id": seed_user["user"].id}


def _ctx(world):
    """A fresh read pass for the world's owner."""
    return BalanceContext.build(world["user_id"])


def _figures(world, *, payday=_PAYDAY, roth="110.00", one_offs=None, base="2884.62"):
    """The worked example's figures, with the named departures.

    Each line is printed under its own kind; :func:`_printed_under` moves one
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
            _tax(WithholdingKindEnum.FEDERAL_INCOME): Decimal("150.00"),
            _tax(WithholdingKindEnum.STATE_INCOME): Decimal("100.00"),
            _tax(WithholdingKindEnum.SOCIAL_SECURITY): Decimal("180.00"),
            _tax(WithholdingKindEnum.MEDICARE): Decimal("42.00"),
        },
        one_offs=(
            (OneOffFigure("Retro pay", _kind(PaycheckLineKindEnum.TAXABLE_EARNING),
                          Decimal("55.00")),)
            if one_offs is None else one_offs
        ),
        notes=None,
    )


def _printed_under(world: dict, figures: StubFigures, **kinds) -> StubFigures:
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
        line_amounts[line_id] = LineFigure(_kind(member), line_amounts[line_id].amount)
    return dataclasses.replace(figures, line_amounts=line_amounts)


def _printed(net=_NET, gross=_GROSS):
    """The totals the stub prints: the worked example's unless named."""
    return PrintedTotals(gross=gross, net=net)


def _record(world, figures=None, printed_net=_NET, printed_gross=_GROSS):
    """Record *figures* (the worked example by default) and commit."""
    stub = pay_stub_service.record_stub(
        world["profile"], figures or _figures(world),
        _printed(printed_net, printed_gross), _ctx(world), _TODAY,
    )
    db.session.commit()
    return stub


class TestWhichDaysCarryAStub:
    """R-SAL49 (a payday the app holds or projects) and R-SAL48 (up to the next)."""

    def test_a_payday_up_to_the_next_one_is_accepted(self, world):
        """03-27 is the next payday on 03-20; 03-13 and the first, 01-02, are past."""
        for day in (date(2026, 3, 27), date(2026, 3, 13), date(2026, 1, 2)):
            assert pay_stub_service.payday_refusal(_ctx(world), day, _TODAY) is None

    def test_a_payday_after_the_next_one_is_refused_until_it_passes(self, world):
        """04-10 is refused on 03-20 and on 03-27 itself, and accepted on 03-28."""
        refusal = pay_stub_service.payday_refusal(_ctx(world), date(2026, 4, 10), _TODAY)
        assert refusal == (
            "2026-04-10 has not been paid yet.  A stub can be entered up to your "
            "next payday, 2026-03-27."
        )
        assert pay_stub_service.payday_refusal(
            _ctx(world), date(2026, 4, 10), date(2026, 3, 27),
        ) is not None
        assert pay_stub_service.payday_refusal(
            _ctx(world), date(2026, 4, 10), date(2026, 3, 28),
        ) is None

    def test_a_day_that_is_not_a_payday_is_refused(self, world):
        """03-20 falls inside the 03-13 paycheck, so it opens none."""
        assert pay_stub_service.payday_refusal(
            _ctx(world), date(2026, 3, 20), _TODAY,
        ) == "2026-03-20 is not one of your paydays."

    def test_a_day_below_the_record_is_refused_as_the_records(self, world):
        """A rhythm payday before 01-02 opens no paycheck the app holds (R-SAL49)."""
        assert pay_stub_service.payday_refusal(
            _ctx(world), date(2025, 12, 19), _TODAY,
        ) == "The app holds no paycheck on 2025-12-19: your pay record starts 2026-01-02."


class TestWhatAStubAddsUpTo:
    """The derived totals and the printed-net check."""

    def test_the_worked_example_adds_up_by_hand(self, world):
        """Each line by its own kind, the one-off by its kind, the waterfall's net."""
        totals = pay_stub_service.totals_of(world["profile"], _figures(world))
        assert totals.gross == Decimal("2999.62")
        assert totals.pre_tax == Decimal("315.00")
        assert totals.taxes == Decimal("472.00")
        assert totals.post_tax == Decimal("110.00")
        assert totals.after_tax == Decimal("0.00")
        assert totals.net == _NET

    def test_an_after_tax_one_off_joins_the_net_and_not_the_gross(self, world):
        """A $20.00 after-tax one-off in place of the taxable one: gross 2944.62, net 2067.62."""
        figures = _figures(world, one_offs=(
            OneOffFigure("Reimbursement", _kind(PaycheckLineKindEnum.AFTER_TAX_EARNING),
                         Decimal("20.00")),
        ))
        totals = pay_stub_service.totals_of(world["profile"], figures)
        assert totals.gross == Decimal("2944.62")
        assert totals.after_tax == Decimal("20.00")
        assert totals.net == Decimal("2067.62")

    def test_a_printed_net_a_cent_off_is_refused_and_nothing_is_written(self, world):
        """The lines make $2,102.62; a stub read as $2,102.63 is a typing slip."""
        with pytest.raises(PayStubRefused) as refused:
            _record(world, printed_net=Decimal("2102.63"))
        assert refused.value.errors == {"printed_net": (
            "The lines add up to $2,102.62, but the stub prints $2,102.63 (a "
            "difference of $0.01).  Check each figure against the stub."
        )}
        # Counted BEFORE any rollback: the query autoflushes, so a stub the
        # refused call had staged would be written and counted here.
        assert db.session.query(PayStub).count() == 0
        db.session.rollback()

    def test_a_line_adds_up_by_the_kind_the_stub_prints_it_under(self, world):
        """R-SAL58: Phone printed as a POST-TAX DEDUCTION, not the line's taxable earning.

        gross 2884.62 + 55.00 = 2939.62; post-tax 110.00 + 60.00 = 170.00;
        net 2939.62 - 315.00 - 472.00 - 170.00 = 1982.62.  The gross R-SAL99
        checks is that 2939.62, and the printed-net check reads the same kinds,
        so that net records and the line's own kind's $2,102.62 is refused.
        """
        figures = _printed_under(
            world, _figures(world), phone=PaycheckLineKindEnum.POST_TAX_DEDUCTION,
        )
        totals = pay_stub_service.totals_of(world["profile"], figures)
        assert (totals.gross, totals.post_tax, totals.net) == (
            Decimal("2939.62"), Decimal("170.00"), Decimal("1982.62"),
        )
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_gross=Decimal("2939.62"))
        assert set(refused.value.errors) == {"printed_net"}
        db.session.rollback()
        assert _record(
            world, figures, printed_net=Decimal("1982.62"), printed_gross=Decimal("2939.62"),
        ) is not None

    def test_re_kinding_a_line_moves_no_saved_stub(self, world):
        """Finding SAL-567: Phone's LINE turns post-tax; the saved stub still nets $2,102.62.

        Until R-SAL58 the stub borrowed each line's kind, and this re-kind
        moved its net to $1,982.62 with no edit to the stub.
        """
        stub_id = _record(world).id
        phone = world["lines"]["phone"]
        phone.paycheck_line_kind_id = _kind(PaycheckLineKindEnum.POST_TAX_DEDUCTION)
        db.session.commit()
        db.session.expire_all()

        stub = db.session.get(PayStub, stub_id)
        figures = pay_stub_service.figures_of(stub)
        assert figures.line_amounts[phone.id].paycheck_line_kind_id == (
            _kind(PaycheckLineKindEnum.TAXABLE_EARNING)
        )
        assert pay_stub_service.totals_of(world["profile"], figures).net == _NET

    def test_a_kind_the_app_does_not_hold_is_not_found(self, world):
        """A tampered line kind is a 404, as a tampered one-off kind is, never a write."""
        unknown = db.session.execute(
            text("SELECT MAX(id) + 1 FROM ref.paycheck_line_kinds"),
        ).scalar()
        figures = _figures(world)
        phone = world["lines"]["phone"].id
        figures.line_amounts[phone] = LineFigure(unknown, Decimal("60.00"))
        with pytest.raises(NotFoundError):
            _record(world, figures)
        db.session.rollback()
        assert db.session.query(PayStub).count() == 0

    def test_a_missing_tax_is_refused_and_the_net_is_not_blamed(self, world):
        """Medicare left out of a stub whose printed net ($2,102.62) is right.

        'The four taxes required' is the service's rule, and the net is not
        checked over an incomplete set: without Medicare the lines sum to
        $2,144.62, and a net refusal would blame figures that are correct.
        """
        figures = _figures(world)
        medicare = _tax(WithholdingKindEnum.MEDICARE)
        del figures.withholdings[medicare]
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures)
        assert refused.value.errors == {
            f"tax-{medicare}": "Enter the stub's Medicare ($0.00 if none).",
        }


class TestThePrintedGross:
    """R-SAL99, "Check the gross too" (finding SAL-590): base pay plus the taxable earnings."""

    def test_a_gross_typed_as_base_pay_is_asked_about(self, world):
        """The stub's gross, $2,999.62, typed into Base pay, its earnings entered again.

        Base pay 2999.62 + Phone 60.00 + Retro pay 55.00 = 3114.62 against the
        printed 2999.62: the $115.00 of taxable earnings is counted twice, and
        the net misses by the same $115.00 (2217.62 against 2102.62), refused
        on its own field.  Base pay equal to the printed gross with that pair
        is asked as the Base pay box OR an earning the stub does not print,
        which makes the same pair (ruling R-SAL111 reworded R-SAL99's "is
        counted twice", which stated the first as fact).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _figures(world, base="2999.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable lines make $3,114.62, but the stub "
                "prints $2,999.62: $115.00 over, and the net is off by the same.  Is "
                "the gross in the Base pay box, or is an earning entered that the "
                "stub does not print?"
            ),
            "printed_net": (
                "The lines add up to $2,217.62, but the stub prints $2,102.62 (a "
                "difference of $115.00).  Check each figure against the stub."
            ),
        }
        # Counted BEFORE any rollback, as the net's refusal test counts.
        assert db.session.query(PayStub).count() == 0
        db.session.rollback()

    def test_an_earning_under_another_kind_is_caught_by_the_gross_alone(self, world):
        """Phone ($60.00, printed as taxable) entered as an AFTER-TAX earning.

        The net balances -- an earning joins the deposit either way, once the
        taxes are typed: 2939.62 - 315.00 - 472.00 - 110.00 + 60.00 = 2102.62
        -- so only the gross sees it: 2884.62 + 55.00 = 2939.62 against the
        printed 2999.62.  The gross is short by exactly the $60.00 entered as
        after-tax, which a "no" job's stub whose gross held a real after-tax
        earning makes too, and so does a gross typed $60.00 high, so the
        refusal names the typed gross, asks after the entry AND names the job's
        setting (rulings R-SAL104 and R-SAL110: the developer's rulings of
        2026-10-04 changed this expectation from R-SAL99's single kind
        question).
        """
        figures = _printed_under(
            world, _figures(world), phone=PaycheckLineKindEnum.AFTER_TAX_EARNING,
        )
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures)
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable lines make $2,939.62, but the stub prints "
                "$2,999.62: $60.00 short, the same as your after-tax earnings.  Check "
                "the gross you typed.  Is one of them taxed on your stub?"
            ),
            "stub_gross_includes_after_tax": (
                "If your stub's gross includes after-tax earnings, set that on your "
                "salary profile."
            ),
        }

    def test_an_after_tax_earning_entered_as_taxable_is_not_blamed_on_base_pay(self, world):
        """A stub with no taxable earning prints Phone ($60.00) AFTER-TAX; it is entered taxable.

        Its gross is its base, 2884.62, so the typed base pay equals the
        printed gross -- the SAL-590 slip's first sign -- but the net balances
        (2884.62 - 315.00 - 472.00 - 110.00 + 60.00 = 2047.62, and the same
        with Phone taxed: 2944.62 - 897.00), so nothing was counted twice and
        the refusal names the difference, never the Base pay box.  The net is
        exact, so the amounts are right and the refusal names what the pair
        allows: the typed gross, and the headings (rulings R-SAL106 and
        R-SAL108 reworded this from R-SAL99's "check the base pay ...").
        """
        figures = _figures(world, one_offs=())
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_net=Decimal("2047.62"),
                    printed_gross=Decimal("2884.62"))
        assert refused.value.errors == {"printed_gross": (
            "Base pay plus your taxable lines make $2,944.62, but the stub prints "
            "$2,884.62 (a difference of $60.00).  Your figures make the stub's net, "
            "so the amounts are right: check the gross you typed, and which earnings "
            "are taxable and which after-tax."
        )}

    def test_a_gross_typed_as_base_pay_without_every_tax_is_not_diagnosed(self, world):
        """The SAL-590 slip with Medicare ($42.00) left out: the net cannot confirm it.

        The lines' net leaves Medicare out, so it misses by $157.00 against
        the gross's $115.00 (2259.62 against 2102.62); without the net's
        matching miss the gross alone cannot tell the slip from a kind slip,
        so the refusal names the $115.00 difference and nothing else (ruling
        R-SAL106: a pair no single mistake makes).
        """
        figures = _figures(world, base="2999.62")
        medicare = _tax(WithholdingKindEnum.MEDICARE)
        del figures.withholdings[medicare]
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures)
        assert refused.value.errors["printed_gross"] == (
            "Base pay plus your taxable lines make $3,114.62, but the stub prints "
            "$2,999.62 (a difference of $115.00).  Check each figure against the stub."
        )
        assert set(refused.value.errors) == {f"tax-{medicare}", "printed_gross"}

    def test_a_base_pay_typo_is_not_called_a_gross_in_the_base_box(self, world):
        """Base pay typed $2,884.72 for $2,884.62: both totals miss by $0.10.

        Gross 2999.72 against 2999.62 and net 2102.72 against 2102.62 -- the
        net's matching miss alone would read as the SAL-590 slip, so the
        base pay differing from the printed gross is what keeps it from naming
        the Base pay box as the gross's; the net off by the same names an
        amount (ruling R-SAL106).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _figures(world, base="2884.72"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable lines make $2,999.72, but the stub "
                "prints $2,999.62 (a difference of $0.10).  The net is off by the "
                "same, so an amount is wrong: check the base pay and each earning's "
                "amount."
            ),
            "printed_net": (
                "The lines add up to $2,102.72, but the stub prints $2,102.62 (a "
                "difference of $0.10).  Check each figure against the stub."
            ),
        }

    def test_the_slip_with_a_zero_tax_left_blank_is_still_asked(self, world):
        """The SAL-590 slip on a stub printing Federal $0.00, the Federal box left blank.

        The stub nets 2102.62 + 150.00 = 2252.62.  Typed: Base pay 2999.62
        (the gross) and no Federal, so the lines make gross 3114.62 and net
        3114.62 - 315.00 - 322.00 - 110.00 = 2367.62: both miss by $115.00,
        exactly as with $0.00 typed, so the slip is asked about (ruling
        R-SAL111's wording) beside the missing tax's own refusal.
        """
        federal = _tax(WithholdingKindEnum.FEDERAL_INCOME)
        figures = _figures(world, base="2999.62")
        del figures.withholdings[federal]
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_net=Decimal("2252.62"))
        assert refused.value.errors == {
            f"tax-{federal}": "Enter the stub's Federal income tax ($0.00 if none).",
            "printed_gross": (
                "Base pay plus your taxable lines make $3,114.62, but the stub "
                "prints $2,999.62: $115.00 over, and the net is off by the same.  Is "
                "the gross in the Base pay box, or is an earning entered that the "
                "stub does not print?"
            ),
        }

    def test_a_printed_gross_a_cent_off_is_refused_on_its_own(self, world):
        """$2,999.63 printed against the lines' $2,999.62; the net is right.

        Ruling R-SAL108's own case: a typo in the gross box leaves the net
        exact, so the gross typed is named beside the headings.
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, printed_gross=Decimal("2999.63"))
        assert refused.value.errors == {"printed_gross": (
            "Base pay plus your taxable lines make $2,999.62, but the stub prints "
            "$2,999.63 (a difference of $0.01).  Your figures make the stub's net, "
            "so the amounts are right: check the gross you typed, and which earnings "
            "are taxable and which after-tax."
        )}

    def test_the_gross_is_checked_without_a_complete_set_of_taxes(self, world):
        """No tax enters the gross, so a missing Medicare does not hold its check back.

        The net is still not checked over the incomplete set (its own rule).
        """
        figures = _figures(world)
        medicare = _tax(WithholdingKindEnum.MEDICARE)
        del figures.withholdings[medicare]
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_gross=Decimal("2999.63"))
        assert set(refused.value.errors) == {f"tax-{medicare}", "printed_gross"}


def _say_yes(world):
    """Set the world's job to "my stub's gross includes after-tax earnings" (R-SAL102)."""
    world["profile"].stub_gross_includes_after_tax = True
    db.session.commit()


def _reimbursed(world, **departures):
    """The worked example with a $20.00 AFTER-TAX one-off "Reimbursement" for Retro pay.

    gross (base + Phone) 2884.62 + 60.00 = 2944.62; after-tax 20.00;
    net 2944.62 - 315.00 - 472.00 - 110.00 + 20.00 = 2067.62.  A stub whose
    gross holds the reimbursement prints 2964.62.
    """
    return _figures(world, one_offs=(
        OneOffFigure("Reimbursement", _kind(PaycheckLineKindEnum.AFTER_TAX_EARNING),
                     Decimal("20.00")),
    ), **departures)


def _reimbursed_without_phone(world, **departures):
    """:func:`_reimbursed` with no taxable earning at all: Phone is not on the stub.

    gross = base 2884.62; after-tax 20.00;
    net 2884.62 - 315.00 - 472.00 - 110.00 + 20.00 = 2007.62.
    """
    figures = _reimbursed(world, **departures)
    del figures.line_amounts[world["lines"]["phone"].id]
    return figures


class TestTheJobSaysWhatItsGrossHolds:
    """R-SAL102 ("A yes/no on each job", finding SAL-592), worded by R-SAL104 and R-SAL106.

    A "yes" job's printed gross is checked as base pay plus the taxable AND
    the after-tax earnings.  How a miss is worded is read off the net
    (R-SAL106): with the net exact, a miss of exactly the after-tax total in
    the direction that fits the job names the typed gross and the setting
    (R-SAL104's question beside them on a "no" job; R-SAL108, R-SAL110);
    with the net off, the pair decides and no setting is named.
    """

    def test_the_door_reads_the_jobs_answer(self, world):
        """The same stub, gross 2964.62 holding the reimbursement: "no" refuses, "yes" saves.

        "no": base + taxable = 2944.62, $20.00 short -- exactly the after-tax
        earnings, with the net exact -- so the refusal names the typed gross,
        asks after the entry and names the setting (R-SAL104, R-SAL110).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed(world), printed_net=Decimal("2067.62"),
                    printed_gross=Decimal("2964.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable lines make $2,944.62, but the stub prints "
                "$2,964.62: $20.00 short, the same as your after-tax earnings.  Check "
                "the gross you typed.  Is one of them taxed on your stub?"
            ),
            "stub_gross_includes_after_tax": (
                "If your stub's gross includes after-tax earnings, set that on your "
                "salary profile."
            ),
        }
        db.session.rollback()
        assert db.session.query(PayStub).count() == 0

        _say_yes(world)
        stub = _record(world, _reimbursed(world), printed_net=Decimal("2067.62"),
                       printed_gross=Decimal("2964.62"))
        assert [(o.name, o.amount) for o in stub.one_offs] == [
            ("Reimbursement", Decimal("20.00")),
        ]

    def test_an_edit_reads_the_stubs_own_job(self, world):
        """A "yes" job's stub takes an edit (Roth 110 -> 100, net +10.00) checked the same way."""
        _say_yes(world)
        stub = _record(world, _reimbursed(world), printed_net=Decimal("2067.62"),
                       printed_gross=Decimal("2964.62"))
        pay_stub_service.edit_stub(
            stub, _reimbursed(world, roth="100.00"),
            _printed(Decimal("2077.62"), Decimal("2964.62")), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert stub.version_id == 2

    def test_a_yes_jobs_gross_that_leaves_them_out_names_the_setting(self, world):
        """"yes", but the stub prints 2944.62: the check's 2964.62 is $20.00 over, net exact.

        On a "yes" job no heading moves the gross, so with every amount right
        the pair allows the setting (ruling R-SAL106) or a typo in the gross
        box (ruling R-SAL110), and the refusal names both.
        """
        _say_yes(world)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed(world), printed_net=Decimal("2067.62"),
                    printed_gross=Decimal("2944.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable and after-tax lines make $2,964.62, but "
                "the stub prints $2,944.62: $20.00 over, the same as your after-tax "
                "earnings, and your figures make the stub's net: check the gross you "
                "typed, or your stub's gross leaves after-tax earnings out."
            ),
            "stub_gross_includes_after_tax": "Set that on your salary profile.",
        }

    def test_a_deduction_entered_as_an_earning_is_named_by_the_net(self, world):
        """"yes", Roth ($110.00, a post-tax deduction) entered as an AFTER-TAX earning.

        The stub prints the worked example (gross 2999.62, net 2102.62).  The
        check makes 2999.62 + 110.00 = 3109.62, $110.00 over -- the after-tax
        total -- but the net misses by TWICE that, 2999.62 - 315.00 - 472.00
        + 110.00 = 2322.62 against 2102.62, which rules the setting out: the
        refusal asks after a deduction entered as an earning and names no
        setting (ruling R-SAL106).
        """
        _say_yes(world)
        figures = _printed_under(
            world, _figures(world), roth=PaycheckLineKindEnum.AFTER_TAX_EARNING,
        )
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures)
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable and after-tax lines make $3,109.62, but "
                "the stub prints $2,999.62: $110.00 over; the net is off by twice "
                "that.  Is a deduction entered as an earning?  Check each line's kind."
            ),
            "printed_net": (
                "The lines add up to $2,322.62, but the stub prints $2,102.62 (a "
                "difference of $220.00).  Check each figure against the stub."
            ),
        }

    def test_a_gross_typed_as_base_pay_on_a_yes_job_is_asked_about(self, world):
        """"yes", the stub's gross 2964.62 typed into Base pay beside its earnings.

        The check makes 2964.62 + 60.00 + 20.00 = 3044.62: $80.00 counted
        twice, and the net misses by the same (2147.62 against 2067.62): asked
        as the Base pay box or an earning the stub does not print (R-SAL111).
        """
        _say_yes(world)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed(world, base="2964.62"),
                    printed_net=Decimal("2067.62"), printed_gross=Decimal("2964.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable and after-tax lines make $3,044.62, but "
                "the stub prints $2,964.62: $80.00 over, and the net is off by the "
                "same.  Is the gross in the Base pay box, or is an earning entered "
                "that the stub does not print?"
            ),
            "printed_net": (
                "The lines add up to $2,147.62, but the stub prints $2,067.62 (a "
                "difference of $80.00).  Check each figure against the stub."
            ),
        }

    def test_the_net_tells_the_doubled_gross_from_the_setting(self, world):
        """With no taxable earning both slips put base pay at the printed gross, $20.00 over.

        A "yes" stub printing base 2884.62 + Reimbursement 20.00 = 2904.62,
        that gross typed as base pay: the check's 2924.62 is $20.00 over AND
        the net misses by $20.00 (2027.62 against 2007.62) -- the Base pay box.
        A stub that leaves the reimbursement out prints 2884.62, typed right:
        the same $20.00 over, but the net balances at 2007.62 -- the setting
        (or the typed gross).  Read without the net, the first would name the
        setting.
        """
        _say_yes(world)
        with pytest.raises(PayStubRefused) as doubled:
            _record(world, _reimbursed_without_phone(world, base="2904.62"),
                    printed_net=Decimal("2007.62"), printed_gross=Decimal("2904.62"))
        assert doubled.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable and after-tax lines make $2,924.62, but "
                "the stub prints $2,904.62: $20.00 over, and the net is off by the "
                "same.  Is the gross in the Base pay box, or is an earning entered "
                "that the stub does not print?"
            ),
            "printed_net": (
                "The lines add up to $2,027.62, but the stub prints $2,007.62 (a "
                "difference of $20.00).  Check each figure against the stub."
            ),
        }
        db.session.rollback()
        with pytest.raises(PayStubRefused) as left_out:
            _record(world, _reimbursed_without_phone(world),
                    printed_net=Decimal("2007.62"), printed_gross=Decimal("2884.62"))
        assert set(left_out.value.errors) == {
            "printed_gross", "stub_gross_includes_after_tax",
        }
        assert "your stub's gross leaves after-tax earnings out." in (
            left_out.value.errors["printed_gross"]
        )

    @pytest.mark.parametrize(("yes", "printed_gross", "message"), [
        (False, "2924.62", (
            "Base pay plus your taxable lines make $2,944.62, but the stub prints "
            "$2,924.62 (a difference of $20.00).  Your figures make the stub's net, "
            "so the amounts are right: check the gross you typed, and which earnings "
            "are taxable and which after-tax."
        )),
        (True, "2984.62", (
            "Base pay plus your taxable and after-tax lines make $2,964.62, but the "
            "stub prints $2,984.62 (a difference of $20.00).  Your figures make the "
            "stub's net, so the amounts are right: check the gross you typed."
        )),
    ], ids=["no-job-over", "yes-job-short"])
    def test_the_after_tax_total_the_wrong_way_names_no_setting(
        self, world, yes, printed_gross, message,
    ):
        """A miss of $20.00 the OTHER way, net exact, names no setting.

        "no": 2924.62 printed is $20.00 UNDER the check (2944.62) -- no
        after-tax earning a gross holds makes that.  "yes": 2984.62 is $20.00
        ABOVE the check (2964.62) -- the reverse of a gross that leaves them
        out.  The net is exact, so the typed gross is named, and on "no" the
        headings too (rulings R-SAL106, R-SAL108); on "yes" no heading moves
        the gross.
        """
        if yes:
            _say_yes(world)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed(world), printed_net=Decimal("2067.62"),
                    printed_gross=Decimal(printed_gross))
        assert refused.value.errors == {"printed_gross": message}

    def test_an_earning_the_stub_does_not_print_is_asked_beside_the_base_box(self, world):
        """A "no" job whose stub prints no taxable earning; Phone ($60.00) entered anyway.

        Base pay is typed right and equals the printed gross, 2884.62; the
        stub nets 2884.62 - 315.00 - 472.00 - 110.00 = 1987.62.  The lines
        make 2884.62 + 60.00 = 2944.62 and net 2047.62: both $60.00 over --
        the pair the gross typed into Base pay makes too, so both are asked
        and neither is stated (ruling R-SAL111; review round 3, MEDIUM-1,
        where "is counted twice" sent the owner to lower base pay and save).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _figures(world, one_offs=()),
                    printed_net=Decimal("1987.62"), printed_gross=Decimal("2884.62"))
        assert refused.value.errors["printed_gross"] == (
            "Base pay plus your taxable lines make $2,944.62, but the stub prints "
            "$2,884.62: $60.00 over, and the net is off by the same.  Is the gross in "
            "the Base pay box, or is an earning entered that the stub does not print?"
        )
        assert set(refused.value.errors) == {"printed_gross", "printed_net"}

    def test_a_base_pay_typo_of_the_after_tax_total_is_an_amount(self, world):
        """A "no" job, base typed 2864.62 for 2884.62 -- $20.00, the after-tax total.

        The stub's gross leaves the reimbursement out (2944.62).  The check
        makes 2864.62 + 60.00 = 2924.62, $20.00 short like R-SAL104's figure,
        but the net is off by the same $20.00 (2047.62 against 2067.62), which
        neither of R-SAL104's causes makes: an amount is wrong, and no setting
        is named (ruling R-SAL106; adversarial review round 1, M-1 (a)).
        """
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed(world, base="2864.62"),
                    printed_net=Decimal("2067.62"), printed_gross=Decimal("2944.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable lines make $2,924.62, but the stub prints "
                "$2,944.62 (a difference of $20.00).  The net is off by the same, so "
                "an amount is wrong: check the base pay and each earning's amount."
            ),
            "printed_net": (
                "The lines add up to $2,047.62, but the stub prints $2,067.62 (a "
                "difference of $20.00).  Check each figure against the stub."
            ),
        }

    def test_the_doubled_gross_beside_a_second_mistake_names_no_setting(self, world):
        """"yes", no taxable earning: the gross 2904.62 typed as base pay AND Roth 100 for 110.

        The check makes 2904.62 + 20.00 = 2924.62, $20.00 over -- the after-tax
        total -- and the net 2904.62 - 315.00 - 472.00 - 100.00 + 20.00 =
        2037.62 is $30.00 off 2007.62: a pair no single mistake makes, so the
        difference alone (ruling R-SAL106; review round 1, M-1 (b), where the
        setting's exit ended in the reimbursement saved as base pay).
        """
        _say_yes(world)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _reimbursed_without_phone(world, base="2904.62", roth="100.00"),
                    printed_net=Decimal("2007.62"), printed_gross=Decimal("2904.62"))
        assert refused.value.errors == {
            "printed_gross": (
                "Base pay plus your taxable and after-tax lines make $2,924.62, but "
                "the stub prints $2,904.62 (a difference of $20.00).  Check each "
                "figure against the stub."
            ),
            "printed_net": (
                "The lines add up to $2,037.62, but the stub prints $2,007.62 (a "
                "difference of $30.00).  Check each figure against the stub."
            ),
        }

    def test_an_earning_entered_as_a_deduction_is_the_mirror(self, world):
        """A "no" job, Phone ($60.00 taxable) entered as a POST-TAX deduction.

        The check makes 2884.62 + 55.00 = 2939.62, $60.00 short of 2999.62,
        and the net 2939.62 - 315.00 - 472.00 - 170.00 = 1982.62 is $120.00
        short of 2102.62 -- twice: an earning entered as a deduction.
        """
        figures = _printed_under(
            world, _figures(world), phone=PaycheckLineKindEnum.POST_TAX_DEDUCTION,
        )
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures)
        assert refused.value.errors["printed_gross"] == (
            "Base pay plus your taxable lines make $2,939.62, but the stub prints "
            "$2,999.62: $60.00 short; the net is off by twice that.  Is an earning "
            "entered as a deduction?  Check each line's kind."
        )
        assert set(refused.value.errors) == {"printed_gross", "printed_net"}

    def test_yes_cannot_see_an_earning_under_the_other_heading(self, world):
        """R-SAL102's accepted cost: Phone (taxed on the stub) entered as AFTER-TAX saves.

        "yes": the check adds the after-tax earnings too, so 2884.62 + 60.00 +
        20.00 = 2964.62 makes the printed gross, and the net balances at
        2884.62 - 897.00 + 80.00 = 2067.62.  Only the stub's page shows the
        slip: Phone's kind beside its paycheck line's.
        """
        _say_yes(world)
        figures = _printed_under(
            world, _reimbursed(world), phone=PaycheckLineKindEnum.AFTER_TAX_EARNING,
        )
        stub = _record(world, figures, printed_net=Decimal("2067.62"),
                       printed_gross=Decimal("2964.62"))
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        phone = {row.name: row for row in report.lines}["Phone Allowance"]
        assert phone.kind_agrees is False


class TestTheOneOffNameClash:
    """R-SAL45, compared ignoring capitals and extra spaces (R-SAL51 (b))."""

    def _refused(self, world, names):
        """Record the example with one-offs named *names*; return the refusals."""
        one_offs = tuple(
            OneOffFigure(name, _kind(PaycheckLineKindEnum.TAXABLE_EARNING), Decimal("0.00"))
            for name in names
        )
        figures = _figures(world, one_offs=one_offs)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, figures, printed_net=_NET - Decimal("55.00"),
                    printed_gross=_GROSS - Decimal("55.00"))
        return refused.value.errors

    def test_a_one_off_named_like_a_paycheck_line_is_refused(self, world):
        """'  health   INSURANCE ' is the Health Insurance line."""
        assert self._refused(world, ["  health   INSURANCE "]) == {"one_off:0": (
            "'  health   INSURANCE ' is your paycheck line 'Health Insurance'; "
            "enter it on the line instead."
        )}

    def test_a_one_off_named_like_a_tax_is_refused(self, world):
        """'medicare' is the Medicare tax."""
        assert self._refused(world, ["medicare"]) == {"one_off:0": (
            "'medicare' is the tax 'Medicare'; enter it with the taxes instead."
        )}

    def test_two_one_offs_differing_only_in_capitals_are_refused(self, world):
        """'Bonus' and 'bonus' are one name; the second is the one refused."""
        assert self._refused(world, ["Bonus", "bonus"]) == {
            "one_off:1": "Two one-offs are named 'bonus'.",
        }

    def test_a_distinct_name_is_stored(self, world):
        """The control: 'Retro pay' clashes with nothing and is recorded."""
        stub = _record(world)
        assert [(o.name, o.amount) for o in stub.one_offs] == [("Retro pay", Decimal("55.00"))]


class TestTheClashIsAskedOnlyOfWhatASaveAdds:
    """R-SAL57, "Check only what a save adds": a saved one-off never blocks its own stub.

    Each case records the worked example (its one-off "Retro pay") and THEN
    gives the profile a paycheck line named "Retro pay", which the line door
    allows ("line names stay free").
    """

    @staticmethod
    def _stub_whose_one_off_a_line_now_names(world):
        """The recorded 03-27 stub, and a $5.00 taxable line named like its one-off."""
        stub = _record(world)
        make_flat_paycheck_line(
            world["profile"], "Retro pay", "5.00", PaycheckLineKindEnum.TAXABLE_EARNING,
        )
        db.session.commit()
        return stub

    @staticmethod
    def _one_offs(*rows):
        """Taxable-earning one-offs from ``(name, amount)`` pairs."""
        return tuple(
            OneOffFigure(name, _kind(PaycheckLineKindEnum.TAXABLE_EARNING), Decimal(amount))
            for name, amount in rows
        )

    def test_an_edit_that_keeps_the_one_off_is_saved(self, world):
        """Roth 110 -> 100 (net +10.00): the kept "Retro pay" does not block the edit."""
        stub = self._stub_whose_one_off_a_line_now_names(world)
        pay_stub_service.edit_stub(
            stub, _figures(world, roth="100.00"), _printed(_NET + 10), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert stub.version_id == 2
        assert [o.name for o in stub.one_offs] == ["Retro pay"]

    def test_re_casing_the_kept_one_off_is_not_a_new_clash(self, world):
        """"RETRO PAY" is the saved one-off's own name in the clash's compared form."""
        stub = self._stub_whose_one_off_a_line_now_names(world)
        pay_stub_service.edit_stub(
            stub, _figures(world, one_offs=self._one_offs(("RETRO PAY", "55.00"))),
            _printed(), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert [o.name for o in stub.one_offs] == ["RETRO PAY"]

    def test_a_one_off_the_edit_adds_is_still_checked(self, world):
        """A second one-off "vision" is new, and is the Vision line."""
        stub = self._stub_whose_one_off_a_line_now_names(world)
        with pytest.raises(PayStubRefused) as refused:
            pay_stub_service.edit_stub(
                stub,
                _figures(world, one_offs=self._one_offs(
                    ("Retro pay", "55.00"), ("vision", "0.00"),
                )),
                _printed(), _ctx(world), _TODAY,
            )
        assert refused.value.errors == {"one_off:1": (
            "'vision' is your paycheck line 'Vision'; enter it on the line instead."
        )}

    def test_renaming_the_one_off_onto_a_lines_name_is_checked(self, world):
        """"Retro pay" renamed "dental" is a name the stub does not hold: the Dental line."""
        stub = self._stub_whose_one_off_a_line_now_names(world)
        with pytest.raises(PayStubRefused) as refused:
            pay_stub_service.edit_stub(
                stub, _figures(world, one_offs=self._one_offs(("dental", "55.00"))),
                _printed(), _ctx(world), _TODAY,
            )
        assert refused.value.errors == {"one_off:0": (
            "'dental' is your paycheck line 'Dental'; enter it on the line instead."
        )}


class TestRecording:
    """What a record writes, and whose keys place it."""

    def test_the_stub_and_its_rows_are_written_under_the_owned_profile(self, world):
        """One stub, four line amounts, four taxes, one one-off; switch on; version 1."""
        stub = _record(world)
        db.session.expire_all()
        stub = db.session.get(PayStub, stub.id)
        assert stub.salary_profile_id == world["profile"].id
        assert stub.use_for_pricing is True
        assert stub.version_id == 1
        assert sorted(
            (row.paycheck_line_id, row.amount, row.salary_profile_id)
            for row in stub.line_amounts
        ) == sorted([
            (world["lines"]["health"].id, Decimal("280.00"), world["profile"].id),
            (world["lines"]["dental"].id, Decimal("35.00"), world["profile"].id),
            (world["lines"]["roth"].id, Decimal("110.00"), world["profile"].id),
            (world["lines"]["phone"].id, Decimal("60.00"), world["profile"].id),
        ])
        assert len(stub.withholdings) == 4
        assert pay_stub_service.totals_of(
            world["profile"], pay_stub_service.figures_of(stub),
        ).net == _NET

    def test_a_line_of_another_profile_is_not_found(self, world, seed_user):
        """A second profile's line id in the figures is a 404, never a write."""
        other = make_salary_profile(seed_user, db.session, name="Side Job")
        db.session.flush()
        foreign = make_flat_paycheck_line(
            other, "Foreign", "1.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
        )
        figures = _figures(world)
        figures = StubFigures(
            payday=figures.payday, base_pay=figures.base_pay,
            line_amounts={
                **figures.line_amounts,
                foreign.id: LineFigure(foreign.paycheck_line_kind_id, Decimal("1.00")),
            },
            withholdings=figures.withholdings, one_offs=figures.one_offs, notes=None,
        )
        with pytest.raises(NotFoundError):
            _record(world, figures, printed_net=_NET - Decimal("1.00"))

    def test_recording_a_held_payday_is_refused(self, world):
        """R-SAL52: a second record of 03-27 (Roth 100) cannot overwrite the first (Roth 110)."""
        first = _record(world)
        with pytest.raises(PayStubRefused) as refused:
            _record(world, _figures(world, roth="100.00"), printed_net=_NET + 10)
        assert refused.value.errors == {"payday": (
            "Your 2026-03-27 stub was entered while this form was open; open it "
            "to change it."
        )}
        db.session.rollback()
        assert db.session.query(PayStub).count() == 1
        roth = world["lines"]["roth"].id
        assert {row.paycheck_line_id: row.amount for row in first.line_amounts}[roth] == (
            Decimal("110.00")
        )


class TestEditing:
    """An edit rewrites the stub row by row and writes the row itself."""

    def test_a_child_only_edit_bumps_the_stubs_version(self, world):
        """Only Roth's amount changes, and the row's counter still moves 1 -> 2."""
        stub = _record(world)
        pay_stub_service.edit_stub(
            stub, _figures(world, roth="100.00"), _printed(_NET + 10), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert stub.version_id == 2

    def test_a_field_and_line_edit_bumps_the_version_once(self, world):
        """Base pay AND Roth change in one edit: 1 -> 2, not 1 -> 3."""
        stub = _record(world)
        pay_stub_service.edit_stub(
            stub, _figures(world, roth="100.00", base="2884.58"),
            _printed(_NET + 10 - Decimal("0.04"), _GROSS - Decimal("0.04")),
            _ctx(world), _TODAY,
        )
        db.session.commit()
        assert stub.version_id == 2

    def test_an_edit_of_a_lines_kind_alone_rewrites_it_and_bumps_once(self, world):
        """Phone re-read as an after-tax earning: its row's kind moves, 1 -> 2.

        The net is unchanged; the gross R-SAL99 checks is $2,939.62, without it.
        """
        stub = _record(world)
        pay_stub_service.edit_stub(
            stub,
            _printed_under(world, _figures(world), phone=PaycheckLineKindEnum.AFTER_TAX_EARNING),
            _printed(gross=Decimal("2939.62")), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert stub.version_id == 2
        phone = world["lines"]["phone"].id
        assert {row.paycheck_line_id: row.paycheck_line_kind_id for row in stub.line_amounts}[
            phone
        ] == _kind(PaycheckLineKindEnum.AFTER_TAX_EARNING)

    def test_an_identical_edit_writes_nothing(self, world):
        """The same figures again: no row changes and the counter stays at 1."""
        stub = _record(world)
        pay_stub_service.edit_stub(stub, _figures(world), _printed(), _ctx(world), _TODAY)
        db.session.commit()
        assert stub.version_id == 1

    def test_a_child_only_edit_races_on_the_stubs_version(self, world):
        """Another writer bumped the row; the child-only edit's flush is refused."""
        stub = _record(world)
        db.session.execute(
            text("UPDATE salary.pay_stubs SET version_id = version_id + 1 WHERE id = :id"),
            {"id": stub.id},
        )
        with pytest.raises(StaleDataError):
            pay_stub_service.edit_stub(
                stub, _figures(world, roth="100.00"), _printed(_NET + 10), _ctx(world), _TODAY,
            )
        db.session.rollback()

    def test_an_edit_onto_a_held_payday_is_refused(self, world):
        """Moving the 03-27 stub onto 03-13, which holds a stub, is refused."""
        _record(world, _figures(world, payday=date(2026, 3, 13)))
        stub = _record(world)
        with pytest.raises(PayStubRefused) as refused:
            pay_stub_service.edit_stub(
                stub, _figures(world, payday=date(2026, 3, 13)), _printed(),
                _ctx(world), _TODAY,
            )
        assert refused.value.errors == {
            "payday": "Your 2026-03-13 stub already exists; open it to change it.",
        }

    def test_a_stub_whose_payday_left_the_record_stays_editable(self, world):
        """R-SAL53: a 03-20 stub (no paycheck there) takes an edit that keeps its date."""
        stub = PayStub(salary_profile_id=world["profile"].id, payday=date(2026, 3, 20),
                       base_pay=Decimal("2884.62"))
        db.session.add(stub)
        db.session.commit()
        pay_stub_service.edit_stub(
            stub, _figures(world, payday=date(2026, 3, 20)), _printed(), _ctx(world), _TODAY,
        )
        db.session.commit()
        assert len(stub.line_amounts) == 4

    def test_an_edit_that_moves_the_date_is_checked(self, world):
        """R-SAL53's other half: moving the 03-27 stub onto 03-20 is refused."""
        stub = _record(world)
        with pytest.raises(PayStubRefused) as refused:
            pay_stub_service.edit_stub(
                stub, _figures(world, payday=date(2026, 3, 20)), _printed(), _ctx(world),
                _TODAY,
            )
        assert refused.value.errors == {"payday": "2026-03-20 is not one of your paydays."}

    def test_an_edit_removes_a_line_and_renames_a_one_off(self, world):
        """Dropping Dental deletes its row; renaming the one-off replaces its row."""
        stub = _record(world)
        figures = _figures(world, one_offs=(
            OneOffFigure("Retro pay adjustment", _kind(PaycheckLineKindEnum.TAXABLE_EARNING),
                         Decimal("55.00")),
        ))
        del figures.line_amounts[world["lines"]["dental"].id]
        pay_stub_service.edit_stub(stub, figures, _printed(_NET + 35), _ctx(world), _TODAY)
        db.session.commit()
        assert db.session.query(PayStubLineAmount).filter_by(
            paycheck_line_id=world["lines"]["dental"].id,
        ).count() == 0
        assert [o.name for o in db.session.query(PayStubOneOff).all()] == [
            "Retro pay adjustment",
        ]

    def test_the_switch_turns_and_an_identical_request_writes_nothing(self, world):
        """Off, then off again (no move), then on."""
        stub = _record(world)
        assert pay_stub_service.set_use_for_pricing(stub, False) is True
        db.session.commit()
        assert pay_stub_service.set_use_for_pricing(stub, False) is False
        assert pay_stub_service.set_use_for_pricing(stub, True) is True
        db.session.commit()
        assert stub.use_for_pricing is True
        assert stub.version_id == 3


class TestTheReport:
    """Each line beside the app's figure that payday, and the base-pay gap."""

    def test_each_line_is_set_beside_the_apps_figure(self, world):
        """Health and Phone agree; Roth differs; Dental is not taken; Vision is not on the stub."""
        stub = _record(world)
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        rows = {row.name: (row.on_stub, row.in_app, row.agrees) for row in report.lines}
        assert rows == {
            "Health Insurance": (Decimal("280.00"), Decimal("280.00"), True),
            "Vision": (None, Decimal("12.00"), False),
            "Dental": (Decimal("35.00"), None, False),
            "Roth IRA": (Decimal("110.00"), Decimal("100.00"), False),
            "Phone Allowance": (Decimal("60.00"), Decimal("60.00"), True),
        }
        assert report.disagreements == 3
        assert report.app_base_pay == Decimal("2884.62")
        assert report.base_gap == Decimal("0.00")
        assert report.totals.net == _NET
        assert [(o.name, o.amount) for o in report.one_offs] == [
            ("Retro pay", Decimal("55.00")),
        ]

    def test_a_kind_the_stub_prints_differently_is_listed(self, world):
        """R-SAL58: Phone printed as an AFTER-TAX earning, the line's a taxable one.

        The net is the same either way -- an earning joins the deposit whether
        it is taxed or not, once the taxes are typed: gross 2939.62, after-tax
        60.00, net 2939.62 - 315.00 - 472.00 - 110.00 + 60.00 = 2102.62.  R-SAL99
        reads the printed gross as base pay plus the TAXABLE earnings, so the
        record is checked against 2939.62, passes both checks, and the REPORT
        is what shows it.  The figures agree; the row still counts as a
        disagreement.
        """
        figures = _printed_under(
            world, _figures(world), phone=PaycheckLineKindEnum.AFTER_TAX_EARNING,
        )
        stub = _record(world, figures, printed_gross=Decimal("2939.62"))
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        phone = {row.name: row for row in report.lines}["Phone Allowance"]
        assert (phone.on_stub, phone.in_app) == (Decimal("60.00"), Decimal("60.00"))
        assert (phone.stub_kind, phone.kind) == (
            PaycheckLineKindEnum.AFTER_TAX_EARNING, PaycheckLineKindEnum.TAXABLE_EARNING,
        )
        assert (phone.stub_kind_label, phone.kind_label) == (
            "After-tax earning", "Taxable earning",
        )
        assert phone.kind_agrees is False
        assert phone.agrees is False
        assert report.disagreements == 4
        assert report.totals.net == _NET

    def test_a_line_the_stub_does_not_print_has_no_kind_to_disagree(self, world):
        """Vision is not on the stub: its row has no stub kind and fails on its figure alone."""
        stub = _record(world)
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        vision = {row.name: row for row in report.lines}["Vision"]
        assert vision.stub_kind is None
        assert vision.stub_kind_label is None
        assert vision.kind_agrees is True
        assert vision.agrees is False

    def test_the_base_gap_is_the_stub_less_the_salary(self, world):
        """A stub printing $2,884.58 base is $0.04 under the salary's $2,884.62."""
        stub = _record(
            world, _figures(world, base="2884.58"),
            printed_net=_NET - Decimal("0.04"), printed_gross=_GROSS - Decimal("0.04"),
        )
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        assert report.base_gap == Decimal("-0.04")

    def test_a_payday_the_app_no_longer_holds_compares_with_nothing(self, world):
        """A stub row on a non-payday (written past the door) reports no paycheck."""
        stub = PayStub(salary_profile_id=world["profile"].id, payday=date(2026, 3, 20),
                       base_pay=Decimal("2884.62"))
        db.session.add(stub)
        db.session.flush()
        report = pay_stub_service.stub_report(world["profile"], stub, _ctx(world))
        assert report.app_base_pay is None
        assert report.base_gap is None
        assert not report.lines

    def test_the_form_splits_the_lines_by_the_apps_payday(self, world):
        """Dental is taken on 03-13 (a month's first paycheck) and not on 03-27."""
        taken_27 = pay_stub_service.form_lines(world["profile"], _ctx(world), _PAYDAY)
        taken_13 = pay_stub_service.form_lines(
            world["profile"], _ctx(world), date(2026, 3, 13),
        )
        assert [line.name for line in taken_27.not_taken] == ["Dental"]
        assert "Dental" in [line.name for line in taken_13.taken]
        assert not taken_13.not_taken

    def test_a_priced_line_carries_its_rows_identity(self, world):
        """The engine's priced lines name the rows they priced, the report's join key."""
        ctx = _ctx(world)
        period = ctx.calendar().period_containing(_PAYDAY)
        breakdown = ctx.paychecks().for_profile(world["profile"]).at(period)
        priced = [*breakdown.earnings.taxable, *breakdown.deductions.pre_tax,
                  *breakdown.deductions.post_tax]
        lines = world["lines"]
        assert {line.paycheck_line_id: line.name for line in priced} == {
            lines["health"].id: "Health Insurance",
            lines["vision"].id: "Vision",
            lines["roth"].id: "Roth IRA",
            lines["phone"].id: "Phone Allowance",
        }


class TestTheLineDeleteRefusal:
    """Fork 10, worded by R-SAL51 (c): every stub that names the line, newest first."""

    def test_a_line_no_stub_names_may_be_deleted(self, world):
        """Vision is on no stub."""
        _record(world)
        assert pay_stub_service.line_delete_refusal(world["lines"]["vision"]) is None

    def test_one_stub_is_named(self, world):
        """Health Insurance on the 03-27 stub only."""
        _record(world)
        assert pay_stub_service.line_delete_refusal(world["lines"]["health"]) == (
            "Health Insurance is on your 2026-03-27 stub; end it instead."
        )

    def test_every_stub_is_named_newest_first(self, world):
        """Health Insurance on 01-02, 03-13 and 03-27."""
        _record(world, _figures(world, payday=date(2026, 3, 13)))
        _record(world, _figures(world, payday=date(2026, 1, 2)))
        _record(world)
        assert pay_stub_service.line_delete_refusal(world["lines"]["health"]) == (
            "Health Insurance is on your 2026-03-27, 2026-03-13 and 2026-01-02 "
            "stubs; end it instead."
        )
