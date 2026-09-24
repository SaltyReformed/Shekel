"""
Shekel Budget App -- the pay stub entry door's routes (plan step salary:S11-b).

The five routes of :mod:`app.routes.salary.stubs` and the paycheck-line delete
refusal, driven through the test client:

* the entry flow picks the payday first (ruling **R-SAL50**): a non-payday is
  refused at step 1, a held payday opens its stub, a free one renders the form
  with the lines the app takes that payday first;
* a record, an edit and the "Use for pricing" switch each write through the
  service and redirect; a refusal re-renders the form with every figure the
  owner typed (a 422), and writes nothing;
* every door is the owner's: another owner's profile or stub is a 404 -- each
  paired with the owner's own request to the SAME ROUTE succeeding, so a 404
  from the URL map cannot pass for the ownership gate.  The attacker is
  ``auth_client`` against the second owner's rows, the shape the suite's own
  IDOR cases use (``test_savings``' ``test_goal_idor_view_blocked``).  A first
  draft attacked with ``second_auth_client`` in a test that also took
  ``auth_client``, and that request RECORDED a stub on the first owner's
  profile (measured 2026-09-23): Flask-Login caches the user on ``g`` inside
  the ``db`` fixture's one app context, so the second client acts as the
  first -- a harness artifact, documented at
  ``tests/test_adversarial/test_totp_replay.py``'s ``_reset_login_cache``
  and filed as N-550;
* the form POSTED here is the form the template EMITS: its field names are
  read off the rendered page and compared with the payload's;
* deleting a paycheck line a stub names is refused with **R-SAL51** (c)'s
  wording, and the line stays.

The figures are the service suite's worked example
(``tests/test_services/test_pay_stub_service.py``): the 03-27 stub nets
``$2,052.62``.  Today is frozen at 2026-03-20, so 03-27 is the next payday.
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PaycheckLineKindEnum, WithholdingKindEnum
from app.extensions import db
from app.models.pay_stub import PayStub
from app.models.paycheck_line import PaycheckLine
from app.services import pay_stub_service
from tests._test_helpers import (
    build_pay_stub_world,
    freeze_today,
    make_flat_paycheck_line,
    make_salary_profile,
)

_PAYDAY = "2026-03-27"


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Today is 2026-03-20: inside the seeded schedule, the 03-27 payday next."""
    freeze_today(monkeypatch, date(2026, 3, 20))


@pytest.fixture(name="world")
def _world(seed_user, seed_periods):  # pylint: disable=unused-argument
    """The worked example's profile and lines, committed; ids only.

    Pylint: ``unused-argument`` -- ``seed_periods`` is requested for the pay
    schedule it writes, which the Dental line's rule and every payday need.
    """
    profile, lines = build_pay_stub_world(seed_user)
    return {
        "profile_id": profile.id,
        "lines": {key: line.id for key, line in lines.items()},
    }


@pytest.fixture(name="victim")
def _victim(seed_second_user):
    """The SECOND owner's profile, one line and one stub, written past the door."""
    profile = make_salary_profile(seed_second_user, db.session, name="Victim Job")
    db.session.flush()
    make_flat_paycheck_line(
        profile, "Victim Line", "10.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
    )
    stub = PayStub(salary_profile_id=profile.id, payday=date(2026, 3, 27),
                   base_pay=Decimal("1000.00"))
    db.session.add(stub)
    db.session.commit()
    return {"profile_id": profile.id, "stub_id": stub.id}


def _tax_field(member):
    """The form field of one tax."""
    return f"tax-{ref_cache.withholding_kind_id(member)}"


def _payload(world, *, payday=_PAYDAY, printed_net="2052.62", roth="110.00",
             one_off=("Retro pay", "55.00")):
    """The worked example as the form posts it: every field the template emits.

    Vision is left blank (the stub does not print it).  A new stub's form
    renders two one-off rows; the first carries the one-off and the second
    is posted blank, as a browser posts it.
    """
    lines = world["lines"]
    taxable = str(ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.TAXABLE_EARNING))
    return {
        "payday": payday,
        "base_pay": "2884.62",
        f"line-{lines['health']}": "310.00",
        f"line-{lines['vision']}": "",
        f"line-{lines['dental']}": "40.00",
        f"line-{lines['roth']}": roth,
        f"line-{lines['phone']}": "45.00",
        _tax_field(WithholdingKindEnum.FEDERAL_INCOME): "150.00",
        _tax_field(WithholdingKindEnum.STATE_INCOME): "100.00",
        _tax_field(WithholdingKindEnum.SOCIAL_SECURITY): "180.00",
        _tax_field(WithholdingKindEnum.MEDICARE): "42.00",
        "one_off_name": [one_off[0], ""],
        "one_off_kind": [taxable, ""],
        "one_off_amount": [one_off[1], ""],
        "notes": "",
        "printed_net": printed_net,
    }


def _record(client, world, **overrides):
    """POST the record door; return the response."""
    return client.post(
        f"/salary/{world['profile_id']}/stubs", data=_payload(world, **overrides),
    )


def _stub():
    """The one stub, freshly read."""
    db.session.expire_all()
    return db.session.query(PayStub).one()


def _stub_on(day):
    """The stub dated *day*, freshly read."""
    db.session.expire_all()
    return db.session.query(PayStub).filter_by(payday=day).one()


def _field_names(html):
    """Every form control name the stub form renders, CSRF excepted."""
    start = html.index('<form method="POST"\n          action="/salary/')
    form = html[start:html.index("</form>", start)]
    return set(re.findall(r'name="([^"]+)"', form)) - {"csrf_token"}


class TestTheEntryFlow:
    """R-SAL50: the payday first, then the form for that payday."""

    def test_step_one_asks_for_the_payday(self, auth_client, world):
        """No payday in the query: the date step."""
        response = auth_client.get(f"/salary/{world['profile_id']}/stubs/new")
        assert response.status_code == 200
        assert b"Which payday is the stub for?" in response.data

    def test_a_day_that_is_not_a_payday_is_refused_at_step_one(self, auth_client, world):
        """03-20 is inside a paycheck: step 1 again, with the reason, as a 422."""
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday=2026-03-20",
        )
        assert response.status_code == 422
        assert b"2026-03-20 is not one of your paydays." in response.data

    def test_a_payday_after_the_next_is_refused_at_step_one(self, auth_client, world):
        """04-10 has not been paid yet on 03-20 (R-SAL48)."""
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday=2026-04-10",
        )
        assert response.status_code == 422
        assert b"2026-04-10 has not been paid yet." in response.data

    def test_a_free_payday_lists_the_lines_the_app_takes_first(self, auth_client, world):
        """03-27: Dental (a month's first paycheck only) is under the other lines."""
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday={_PAYDAY}",
        )
        assert response.status_code == 200
        html = response.data.decode()
        taken, others = html.split("Your other paycheck lines")
        assert "Health Insurance" in taken and "Dental" not in taken
        assert "Dental" in others

    def test_the_payload_is_the_form_the_template_emits(self, auth_client, world):
        """Every name the rendered form carries, and no other, is posted."""
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday={_PAYDAY}",
        )
        assert _field_names(response.data.decode()) == set(_payload(world))

    def test_a_held_payday_the_record_no_longer_holds_still_opens(
        self, auth_client, world,
    ):
        """A stub dated 03-20 (written past the door) opens instead of being refused."""
        stub = PayStub(salary_profile_id=world["profile_id"], payday=date(2026, 3, 20),
                       base_pay=Decimal("2884.62"))
        db.session.add(stub)
        db.session.commit()
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday=2026-03-20",
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/salary/stubs/{stub.id}")

    def test_a_held_payday_opens_its_stub(self, auth_client, world):
        """Entering 03-27 again goes to the saved stub (fork 4)."""
        assert _record(auth_client, world).status_code == 302
        response = auth_client.get(
            f"/salary/{world['profile_id']}/stubs/new?payday={_PAYDAY}",
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/salary/stubs/{_stub().id}")


class TestRecording:
    """The record door: written on a clean form, refused whole on a bad one."""

    def test_a_clean_form_records_the_stub_and_shows_its_report(self, auth_client, world):
        """Saved, redirected to its page, which reports three disagreeing lines."""
        response = _record(auth_client, world)
        assert response.status_code == 302
        stub = _stub()
        assert stub.payday == date(2026, 3, 27)
        assert stub.salary_profile_id == world["profile_id"]
        assert len(stub.line_amounts) == 4
        assert [o.name for o in stub.one_offs] == ["Retro pay"]
        page = auth_client.get(response.headers["Location"])
        assert page.status_code == 200
        assert b"$2,052.62" in page.data
        assert b"3 paycheck lines" in page.data

    def test_a_wrong_printed_net_rerenders_the_typed_form_and_writes_nothing(
        self, auth_client, world,
    ):
        """$2,052.63 is refused as a 422 that keeps every figure typed."""
        response = _record(auth_client, world, printed_net="2052.63")
        assert response.status_code == 422
        assert b"The lines add up to $2,052.62, but the stub prints $2,052.63" in response.data
        assert b'value="110.00"' in response.data
        assert b'value="Retro pay"' in response.data
        assert db.session.query(PayStub).count() == 0

    def test_a_missing_tax_is_refused(self, auth_client, world):
        """Every tax is required ($0.00 allowed)."""
        payload = _payload(world)
        payload[_tax_field(WithholdingKindEnum.MEDICARE)] = ""
        response = auth_client.post(f"/salary/{world['profile_id']}/stubs", data=payload)
        assert response.status_code == 422
        assert b"Enter the stub&#39;s Medicare ($0.00 if none)." in response.data
        assert db.session.query(PayStub).count() == 0

    def test_a_one_off_named_like_a_line_is_refused_on_its_row(self, auth_client, world):
        """'health insurance' is refused, and the message sits in that row."""
        response = _record(auth_client, world, one_off=("health insurance", "0.00"),
                           printed_net="1997.62")
        assert response.status_code == 422
        html = response.data.decode()
        row = html.index('value="health insurance"')
        message = html.index("enter it on the line instead.")
        next_row = html.index('name="one_off_name"', row)
        assert row < message < next_row
        assert db.session.query(PayStub).count() == 0

    def test_a_held_payday_is_refused_with_a_link_and_the_typed_figures(
        self, auth_client, world,
    ):
        """R-SAL52: a second 03-27 form (Roth 100) is refused; the stub keeps Roth 110."""
        _record(auth_client, world)
        response = _record(auth_client, world, roth="100.00", printed_net="2062.62")
        assert response.status_code == 422
        html = response.data.decode()
        assert "Your 2026-03-27 stub was entered while this form was open" in html
        assert f'href="/salary/stubs/{_stub().id}"' in html
        roth = world["lines"]["roth"]
        roth_input = html[html.index(f'name="line-{roth}"'):]
        roth_input = roth_input[:roth_input.index(">")]
        assert 'value="100.00"' in roth_input
        assert "110.00" not in roth_input
        assert {r.paycheck_line_id: r.amount for r in _stub().line_amounts}[roth] == (
            Decimal("110.00")
        )

    def test_a_record_racing_past_the_check_meets_the_same_refusal(
        self, auth_client, world, monkeypatch,
    ):
        """Two records of 03-27 that both pass the check: the unique key answers R-SAL52."""
        _record(auth_client, world)
        monkeypatch.setattr(pay_stub_service, "stub_on", lambda profile, day: None)
        response = _record(auth_client, world, roth="100.00", printed_net="2062.62")
        assert response.status_code == 422
        assert b"Your 2026-03-27 stub was entered while this form was open" in response.data
        monkeypatch.undo()
        assert db.session.query(PayStub).count() == 1

    def test_a_date_past_the_calendar_is_refused_not_a_crash(self, auth_client, world):
        """9999-12-31 would overflow the calendar's projection; it is a 422."""
        url = f"/salary/{world['profile_id']}/stubs"
        assert auth_client.get(f"{url}/new?payday=9999-12-31").status_code == 422
        assert _record(auth_client, world, payday="9999-12-31").status_code == 422
        assert db.session.query(PayStub).count() == 0

    def test_an_input_for_another_profiles_line_is_not_read(
        self, auth_client, world, seed_user,
    ):
        """A posted field named for a foreign line is ignored, never stored."""
        other = make_salary_profile(seed_user, db.session, name="Side Job")
        db.session.flush()
        foreign = make_flat_paycheck_line(
            other, "Foreign", "1.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
        )
        db.session.commit()
        payload = _payload(world)
        payload[f"line-{foreign.id}"] = "1.00"
        response = auth_client.post(f"/salary/{world['profile_id']}/stubs", data=payload)
        assert response.status_code == 302
        assert foreign.id not in {row.paycheck_line_id for row in _stub().line_amounts}

    def test_another_owners_profile_is_not_found(self, auth_client, world, victim):
        """The owner's record and entry requests on the SECOND owner's profile 404.

        Paired with the same two routes on the owner's own profile succeeding,
        so the 404 is the ownership gate's and not the URL map's.
        """
        foreign = f"/salary/{victim['profile_id']}/stubs"
        refused = auth_client.post(foreign, data=_payload(world))
        assert refused.status_code == 404, refused.headers.get("Location")
        assert auth_client.get(f"{foreign}/new?payday={_PAYDAY}").status_code == 404
        assert db.session.query(PayStub).filter_by(
            salary_profile_id=victim["profile_id"],
        ).count() == 1
        own = f"/salary/{world['profile_id']}/stubs"
        assert auth_client.get(f"{own}/new?payday={_PAYDAY}").status_code == 200
        assert _record(auth_client, world).status_code == 302


class TestEditing:
    """The edit door, version-checked."""

    def _edit(self, client, world, stub, **overrides):
        """POST the edit door with the stub's current version unless overridden."""
        payload = _payload(world, **{k: v for k, v in overrides.items() if k != "version_id"})
        payload["version_id"] = str(overrides.get("version_id", stub.version_id))
        return client.post(f"/salary/stubs/{stub.id}/edit", data=payload)

    def test_the_payload_is_the_form_the_edit_page_emits(self, auth_client, world):
        """The saved stub's page renders the same controls plus the version."""
        _record(auth_client, world)
        page = auth_client.get(f"/salary/stubs/{_stub().id}")
        assert _field_names(page.data.decode()) == set(_payload(world)) | {"version_id"}

    def test_an_edit_rewrites_the_stub(self, auth_client, world):
        """Roth 110 -> 100, so the stub nets $2,062.62."""
        _record(auth_client, world)
        response = self._edit(auth_client, world, _stub(), roth="100.00",
                              printed_net="2062.62")
        assert response.status_code == 302
        roth = world["lines"]["roth"]
        assert {r.paycheck_line_id: r.amount for r in _stub().line_amounts}[roth] == (
            Decimal("100.00")
        )

    def test_a_stale_invalid_form_is_turned_away_as_stale(self, auth_client, world):
        """A version-1 form with an unreadable base pay, after the stub moved to 2: stale.

        The route refuses 'abc' itself, so were the fields read first the
        answer would be a 422 re-render re-pinned to version 2, and that
        form's next save would overwrite the other edit unseen.
        """
        _record(auth_client, world)
        stub = _stub()
        assert self._edit(auth_client, world, stub, roth="100.00",
                          printed_net="2062.62").status_code == 302
        payload = _payload(world)
        payload["base_pay"] = "abc"
        payload["version_id"] = "1"
        response = auth_client.post(f"/salary/stubs/{stub.id}/edit", data=payload)
        assert response.status_code == 302
        roth = world["lines"]["roth"]
        assert {r.paycheck_line_id: r.amount for r in _stub().line_amounts}[roth] == (
            Decimal("100.00")
        )

    def test_an_edit_onto_a_held_payday_links_that_stub(self, auth_client, world):
        """Moving the 03-27 stub onto 03-13, which holds one, is refused with its link."""
        _record(auth_client, world, payday="2026-03-13")
        held = _stub_on(date(2026, 3, 13))
        _record(auth_client, world)
        stub = _stub_on(date(2026, 3, 27))
        response = self._edit(auth_client, world, stub, payday="2026-03-13")
        assert response.status_code == 422
        assert b"Your 2026-03-13 stub already exists" in response.data
        assert f'href="/salary/stubs/{held.id}"'.encode() in response.data

    def test_a_form_without_its_version_is_a_bad_request(self, auth_client, world):
        """The edit form always carries its version; one without is refused."""
        _record(auth_client, world)
        response = auth_client.post(f"/salary/stubs/{_stub().id}/edit", data=_payload(world))
        assert response.status_code == 400

    def test_a_stale_version_is_a_conflict_and_writes_nothing(self, auth_client, world):
        """A form claiming version 7 of a version-1 stub is turned away."""
        _record(auth_client, world)
        response = self._edit(auth_client, world, _stub(), roth="100.00",
                              printed_net="2062.62", version_id=7)
        assert response.status_code == 302
        roth = world["lines"]["roth"]
        assert {r.paycheck_line_id: r.amount for r in _stub().line_amounts}[roth] == (
            Decimal("110.00")
        )

    def test_another_owners_stub_is_not_found(self, auth_client, world, victim):
        """The SECOND owner's stub 404s on view and edit; the owner's own is served."""
        _record(auth_client, world)
        own = db.session.query(PayStub).filter_by(
            salary_profile_id=world["profile_id"],
        ).one()
        payload = _payload(world)
        payload["version_id"] = "1"
        assert auth_client.get(f"/salary/stubs/{victim['stub_id']}").status_code == 404
        assert auth_client.post(
            f"/salary/stubs/{victim['stub_id']}/edit", data=payload,
        ).status_code == 404
        db.session.expire_all()
        assert db.session.get(PayStub, victim["stub_id"]).base_pay == Decimal("1000.00")
        assert auth_client.get(f"/salary/stubs/{own.id}").status_code == 200


class TestTheSwitch:
    """R-SAL51 (a): each stub's own "Use for pricing" button."""

    def _switch(self, client, stub_id, state, version):
        """POST the switch."""
        return client.post(
            f"/salary/stubs/{stub_id}/pricing",
            data={"use_for_pricing": state, "version_id": str(version)},
        )

    def test_off_then_on_keeps_the_stub(self, auth_client, world):
        """Off keeps the stub; on restores the switch; nothing is deleted."""
        _record(auth_client, world)
        stub = _stub()
        assert self._switch(auth_client, stub.id, "off", 1).status_code == 302
        assert _stub().use_for_pricing is False
        assert self._switch(auth_client, stub.id, "on", 2).status_code == 302
        assert _stub().use_for_pricing is True
        assert db.session.query(PayStub).count() == 1

    def test_a_stale_switch_is_a_conflict(self, auth_client, world):
        """A button rendered at version 5 moves nothing."""
        _record(auth_client, world)
        assert self._switch(auth_client, _stub().id, "off", 5).status_code == 302
        assert _stub().use_for_pricing is True

    def test_a_malformed_switch_is_a_bad_request(self, auth_client, world):
        """A state other than on/off is refused before any read of it."""
        _record(auth_client, world)
        assert self._switch(auth_client, _stub().id, "maybe", 1).status_code == 400

    def test_another_owners_switch_is_not_found(self, auth_client, world, victim):
        """The SECOND owner's switch 404s and stays on; the owner's own turns."""
        _record(auth_client, world)
        own_id = db.session.query(PayStub).filter_by(
            salary_profile_id=world["profile_id"],
        ).one().id
        assert self._switch(auth_client, victim["stub_id"], "off", 1).status_code == 404
        db.session.expire_all()
        assert db.session.get(PayStub, victim["stub_id"]).use_for_pricing is True
        assert self._switch(auth_client, own_id, "off", 1).status_code == 302


    def test_the_profile_page_lists_the_stub_with_its_button(self, auth_client, world):
        """The Pay stubs card: the payday, its net and the switch."""
        _record(auth_client, world)
        page = auth_client.get(f"/salary/{world['profile_id']}/edit")
        assert page.status_code == 200
        assert b"Pay stubs" in page.data
        assert b"2026-03-27" in page.data
        assert b"$2,052.62 net" in page.data
        assert b"Used for pricing: turn off" in page.data


class TestTheLineDelete:
    """Fork 10: a line a stub names is refused, worded by R-SAL51 (c)."""

    def test_a_line_a_stub_names_is_refused_and_stays(self, auth_client, world):
        """Health Insurance is on the 03-27 stub."""
        _record(auth_client, world)
        health = world["lines"]["health"]
        response = auth_client.post(
            f"/salary/lines/{health}/delete", follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Health Insurance is on your 2026-03-27 stub; end it instead." in response.data
        db.session.expire_all()
        assert db.session.get(PaycheckLine, health) is not None

    def test_the_refusal_shows_inside_the_htmx_swap(self, auth_client, world):
        """The trash button posts over HTMX; the refusal is in the swapped section."""
        _record(auth_client, world)
        health = world["lines"]["health"]
        response = auth_client.post(
            f"/salary/lines/{health}/delete", headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        html = response.data.decode()
        assert 'id="lines-section"' in html
        assert "Health Insurance is on your 2026-03-27 stub; end it instead." in html
        db.session.expire_all()
        assert db.session.get(PaycheckLine, health) is not None

    def test_a_line_no_stub_names_is_still_deleted(self, auth_client, world):
        """Vision is on no stub."""
        _record(auth_client, world)
        vision = world["lines"]["vision"]
        assert auth_client.post(f"/salary/lines/{vision}/delete").status_code == 302
        db.session.expire_all()
        assert db.session.get(PaycheckLine, vision) is None
