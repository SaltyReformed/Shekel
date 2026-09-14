"""Every door that asks for a rhythm offers its KIND, and stores what it says.

Plan step ``pay_calendar:C17-d-3``, ruling **R-PC84**: the four doors that
state a pay rhythm -- registration, the generate card, regenerate and reset --
render three RADIO ARMS (every N days, monthly on a day, twice a month on two
days), each holding its own number boxes, all of them submitted, and the
schema builds the :class:`~app.services.pay_rhythm.Rhythm` from the chosen
arm.  That is ONE claim about FOUR surfaces, so it is graded in one file, on
the pattern ``test_pay_schedule_shift_doors.py`` set for the convention.

**Each case drives HTTP rather than the service**, because the path the
ruling is about is the browser's: the arms are rendered from one macro, the
checked arm's token is posted as ``cadence_kind`` alongside EVERY rendered box
(:func:`~tests._test_helpers.cadence_form_values` spells that by hand),
:class:`~app.schemas.validation._pay_rhythm.CadenceKindField` maps the token
to a type, :func:`~app.schemas.validation._pay_rhythm.rhythm_from_wire` reads
the chosen arm and no other, and the writer stores the era.  A break anywhere
in that chain reads to a semi-monthly owner as "I said the 1st and the 15th and
the app put me on a fortnight".

The clock is FROZEN at 2026-06-15 (a Monday) so every on-grid day below is
a literal the reader can check against the rhythm it is posted with.
"""
import re
from datetime import date

import pytest

from app.enums import BusinessDayShiftEnum
from app.models.user import User
from app.services import pay_schedule_service
from app.services.pay_rhythm import FixedDays, Monthly, Rhythm, SemiMonthly
from tests._test_helpers import (
    add_txn,
    all_periods,
    cadence_form_values,
    freeze_today,
    rebuild_calendar_on,
    record_paydays_across_a_hole,
    register_form_data,
    rhythm_of,
    shift_form_value,
)

FROZEN_TODAY = date(2026, 6, 15)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch):
    """Pin every clock to :data:`FROZEN_TODAY`, as the admin route tests do."""
    freeze_today(monkeypatch, FROZEN_TODAY)


def _stored_cadence(user_id):
    """Return the cadence VALUE the owner's latest era states."""
    return pay_schedule_service.resolve_schedule(user_id).rhythm.cadence


def _spanning_periods(db_session, seed_user, count=6):
    """Record ``count`` fortnightly paydays from 06-05, the paycheck holding today.

    The 06-05 paycheck has started and is kept by a regenerate, so the plan's
    next payday is 06-19 and a rebuild may open in ``[06-19, 07-03)`` (plan
    step ``pay_calendar:C17-c-2a``, ruling **R-PC67**) -- the window every
    regenerate below opens a month kind inside.
    """
    periods = record_paydays_across_a_hole(
        seed_user["user"].id, date(2026, 6, 5), count, rhythm_of(14),
    )
    db_session.commit()
    return periods


def _checked(page: bytes, control_id: str) -> bool:
    """Return whether the radio with *control_id* renders ``checked``."""
    return re.search(
        rb'id="' + control_id.encode() + rb'"[^>]*\bchecked\b', page,
    ) is not None


def _box_value(page: bytes, control_id: str) -> bytes:
    """Return the ``value=`` the number box with *control_id* renders."""
    match = re.search(
        rb'id="' + control_id.encode() + rb'"[^>]*value="([^"]*)"', page,
    )
    assert match is not None, f"no box with id {control_id!r} on the page"
    return match.group(1)


class TestTheKindControlIsRenderedOnAllFourDoors:
    """The question is ASKED, which is the half a POST test cannot see.

    A schema that accepted the kind while no form rendered the arms would
    pass every persistence case below and leave the owner unable to answer
    -- finding **P29**'s shape, one control over.  Counted EXACTLY, for the
    reason the convention's census gives: a ``>=`` lets a form lose its arms
    and stay green.
    """

    ARMS = (b"fixed_days", b"monthly", b"semi_monthly")
    BOXES = (
        b"cadence_days", b"day_of_month", b"first_day_of_month",
        b"second_day_of_month",
    )

    def test_registration_offers_the_three_arms_and_every_box(self, client):
        """Sign-up asks the kind before the owner has a schedule to correct."""
        page = client.get("/register").data
        assert page.count(b'name="cadence_kind"') == 3
        for token in self.ARMS:
            assert b'value="' + token + b'"' in page
        for box in self.BOXES:
            assert page.count(b'name="' + box + b'"') == 1

    def test_the_generate_card_offers_them_to_an_owner_with_no_rhythm(
        self, bare_auth_client,
    ):
        """One group on the settings page: generate, and never the manage card."""
        page = bare_auth_client.get("/settings?section=pay-periods").data
        assert page.count(b'name="cadence_kind"') == 3
        for box in self.BOXES:
            assert page.count(b'name="' + box + b'"') == 1

    def test_regenerate_and_reset_each_offer_them_with_distinct_ids(
        self, auth_client,
    ):
        """Two groups on one page, and their ids do not collide.

        Regenerate and reset both render the macro, so without a prefix the
        second form's ``<label for=>`` would check the FIRST form's radio.
        The ``name=`` attributes are the same on both, as the wire requires.
        """
        page = auth_client.get("/settings?section=pay-periods").data
        assert page.count(b'name="cadence_kind"') == 6
        for box in self.BOXES:
            assert page.count(b'name="' + box + b'"') == 2
        for prefix in (b"rg", b"rs"):
            for token in self.ARMS:
                assert page.count(
                    b'id="' + prefix + b"-cadence_kind_" + token + b'"',
                ) == 1

    def test_the_standalone_generate_page_renders_the_arms_on_a_422(
        self, bare_auth_client,
    ):
        """``pay_periods/generate.html`` carries the macro with no stored rhythm.

        Reached through the 422 re-render, as the convention's census is:
        the page has no ``pp_rhythm`` in its context, so this exercises the
        macro's no-stored-rhythm arm and the ``request.form`` echo -- the
        owner's typed day comes back in its box.
        """
        response = bare_auth_client.post("/pay-periods/generate", data={
            "start_date": "not-a-date",
            "num_periods": "3",
            **cadence_form_values(Monthly(15)),
            "shift": shift_form_value(),
        })

        assert response.status_code == 422
        assert response.data.count(b'name="cadence_kind"') == 3
        assert _checked(response.data, "cadence_kind_monthly")
        assert _box_value(response.data, "day_of_month") == b"15"


class TestTheManageCardPreselectsTheStoredRhythm:
    """Regenerate and reset open on the owner's STORED kind and its days.

    The argument the convention select already makes: these forms post the
    whole rhythm, so arms that opened on "every 14 days" would put a monthly
    owner back on a fortnight on every regenerate run for an unrelated
    reason.  Graded as the rendered ``checked`` radio and the box values,
    TWICE on the page.
    """

    def test_a_monthly_owner_sees_the_monthly_arm_checked_with_their_day(
        self, app, auth_client, seed_user,
    ):
        """Monthly on the 15th: that radio, that box, on both forms."""
        with app.app_context():
            rebuild_calendar_on(
                seed_user["user"].id, date(2026, 6, 15), 2,
                Rhythm(cadence=Monthly(15), shift=BusinessDayShiftEnum.NONE),
            )
            page = auth_client.get("/settings?section=pay-periods").data

            for prefix in ("rg", "rs"):
                assert _checked(page, f"{prefix}-cadence_kind_monthly")
                assert not _checked(page, f"{prefix}-cadence_kind_fixed_days")
                assert not _checked(page, f"{prefix}-cadence_kind_semi_monthly")
                assert _box_value(page, f"{prefix}-day_of_month") == b"15"
                # The unchosen arms carry the form's own defaults, not the
                # owner's: the fortnight box its app default, the pair blank.
                assert _box_value(page, f"{prefix}-cadence_days") == str(
                    app.config["DEFAULT_PAY_CADENCE_DAYS"],
                ).encode()
                assert _box_value(page, f"{prefix}-first_day_of_month") == b""
                assert _box_value(page, f"{prefix}-second_day_of_month") == b""

    def test_a_semi_monthly_owner_sees_both_days_lower_first(
        self, app, auth_client, seed_user,
    ):
        """The 15th and the 1st, stated in that order, render as 1 and 15."""
        with app.app_context():
            rebuild_calendar_on(
                seed_user["user"].id, date(2026, 6, 15), 2,
                Rhythm(
                    cadence=SemiMonthly((15, 1)),
                    shift=BusinessDayShiftEnum.NONE,
                ),
            )
            page = auth_client.get("/settings?section=pay-periods").data

            for prefix in ("rg", "rs"):
                assert _checked(page, f"{prefix}-cadence_kind_semi_monthly")
                assert _box_value(page, f"{prefix}-first_day_of_month") == b"1"
                assert _box_value(page, f"{prefix}-second_day_of_month") == b"15"
                assert _box_value(page, f"{prefix}-day_of_month") == b""

    def test_a_fixed_days_owner_sees_their_own_count_not_the_default(
        self, app, auth_client, seed_user,
    ):
        """A weekly owner's forms open on 7, where they opened on a literal 14.

        The old cadence box was ``value="14"`` on both manage forms whatever
        the owner was paid on, so a weekly owner correcting a date resubmitted
        a fortnight unless they noticed.  The stored rhythm is what the arms
        open on now, for every kind.
        """
        with app.app_context():
            rebuild_calendar_on(
                seed_user["user"].id, date(2026, 6, 15), 2,
                Rhythm(cadence=FixedDays(7), shift=BusinessDayShiftEnum.NONE),
            )
            page = auth_client.get("/settings?section=pay-periods").data

            for prefix in ("rg", "rs"):
                assert _checked(page, f"{prefix}-cadence_kind_fixed_days")
                assert _box_value(page, f"{prefix}-cadence_days") == b"7"


class TestEachDoorPersistsTheKind:
    """The chosen kind and its days reach ``budget.pay_eras``."""

    def test_registration_stores_a_monthly_rhythm(self, app, db, client):
        """Sign-up on the 15th of every month, last paid today, the 15th."""
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="monthly-register@example.com",
                display_name="Monthly Register",
                last_payday=FROZEN_TODAY.isoformat(),
                **cadence_form_values(Monthly(15)),
            ))

            assert response.status_code == 302, response.data
            user = db.session.query(User).filter_by(
                email="monthly-register@example.com",
            ).one()
            assert _stored_cadence(user.id) == Monthly(15)

    def test_registration_stores_a_semi_monthly_rhythm(self, app, db, client):
        """Sign-up on the 1st and the 15th, last paid on the 15th."""
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="semi-register@example.com",
                display_name="Semi Register",
                last_payday=FROZEN_TODAY.isoformat(),
                **cadence_form_values(SemiMonthly((1, 15))),
            ))

            assert response.status_code == 302, response.data
            user = db.session.query(User).filter_by(
                email="semi-register@example.com",
            ).one()
            assert _stored_cadence(user.id) == SemiMonthly((1, 15))

    def test_registration_still_defaults_to_the_fortnight_when_nobody_answers(
        self, app, db, client,
    ):
        """An old client posting no kind and no day count means what it meant.

        The five wire keys default TOGETHER: absent, they read as every
        ``DEFAULT_PAY_CADENCE_DAYS`` days, which is what the single box
        meant until this step.
        """
        with app.app_context():
            body = register_form_data(
                email="default-register@example.com",
                display_name="Default Register",
                last_payday=FROZEN_TODAY.isoformat(),
            )
            for key in cadence_form_values():
                del body[key]
            response = client.post("/register", data=body)

            assert response.status_code == 302, response.data
            user = db.session.query(User).filter_by(
                email="default-register@example.com",
            ).one()
            assert _stored_cadence(user.id) == FixedDays(
                app.config["DEFAULT_PAY_CADENCE_DAYS"],
            )

    def test_generate_stores_a_semi_monthly_rhythm(
        self, app, bare_auth_client, bare_user,
    ):
        """The first-schedule door: the 1st and the 15th from 06-01, three paydays."""
        with app.app_context():
            response = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-06-01",
                "num_periods": "3",
                **cadence_form_values(SemiMonthly((1, 15))),
                "shift": shift_form_value(),
            })

            assert response.status_code == 302, response.data
            user_id = bare_user["user"].id
            assert _stored_cadence(user_id) == SemiMonthly((1, 15))
            assert [p.start_date for p in all_periods(user_id)] == [
                date(2026, 6, 1), date(2026, 6, 15), date(2026, 7, 1),
            ]

    def test_regenerate_stores_a_monthly_rhythm(
        self, app, db, auth_client, seed_user,
    ):
        """Correcting the tail onto the 25th of every month, from 06-25."""
        user_id = seed_user["user"].id
        with app.app_context():
            _spanning_periods(db.session, seed_user)
            response = auth_client.post("/pay-periods/regenerate", data={
                "new_start_date": "2026-06-25",
                "num_periods": "3",
                **cadence_form_values(Monthly(25)),
                "shift": shift_form_value(),
            })

            assert response.status_code == 302, response.data
            db.session.expire_all()
            assert _stored_cadence(user_id) == Monthly(25)
            assert [p.start_date for p in all_periods(user_id)][-3:] == [
                date(2026, 6, 25), date(2026, 7, 25), date(2026, 8, 25),
            ]

    def test_reset_stores_a_semi_monthly_rhythm(
        self, app, db, auth_client, seed_user,
    ):
        """The first-time-setup correction: the 5th and the 20th, from 06-05."""
        user_id = seed_user["user"].id
        with app.app_context():
            response = auth_client.post("/pay-periods/reset", data={
                "new_start_date": "2026-06-05",
                "num_periods": "4",
                **cadence_form_values(SemiMonthly((5, 20))),
                "shift": shift_form_value(),
                "confirm": "true",
            })

            assert response.status_code == 302, response.data
            db.session.expire_all()
            assert _stored_cadence(user_id) == SemiMonthly((5, 20))
            assert [p.start_date for p in all_periods(user_id)] == [
                date(2026, 6, 5), date(2026, 6, 20),
                date(2026, 7, 5), date(2026, 7, 20),
            ]

    def test_an_unchosen_arm_is_not_read(self, app, bare_auth_client, bare_user):
        """A stale number in an arm the owner did not choose changes nothing.

        Every arm's box is submitted, so a box the owner typed into and then
        abandoned rides along.  The chosen arm is what the value is built
        from; a blank in another arm is not a missing input either.
        """
        with app.app_context():
            response = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-06-15",
                "num_periods": "2",
                **cadence_form_values(Monthly(15)),
                "cadence_days": "",
                "first_day_of_month": "3",
                "second_day_of_month": "",
                "shift": shift_form_value(),
            })

            assert response.status_code == 302, response.data
            assert _stored_cadence(bare_user["user"].id) == Monthly(15)


class TestARefusalNamesTheControl:
    """Every refusal of a stated rhythm lands on the control at fault.

    Graded on the generate door, the one that renders field errors; the
    manage doors flash ``field: message`` and are graded once each for the
    field they name.  Each message is the write door's own sentence, asked
    through the schema rather than restated (the same discipline the
    convention's floor already follows).
    """

    def _generate(self, client, **overrides):
        """POST the generate form with the fortnight default, overridden."""
        return client.post("/pay-periods/generate", data={
            "start_date": "2026-06-15",
            "num_periods": "2",
            **cadence_form_values(),
            "shift": shift_form_value(),
            **overrides,
        })

    def test_a_blank_box_on_the_chosen_arm_is_refused_on_that_box(
        self, bare_auth_client,
    ):
        """Monthly chosen, its day blank: the day box, not the kind."""
        response = self._generate(
            bare_auth_client, cadence_kind="monthly", day_of_month="",
        )
        assert response.status_code == 422
        assert b"Enter the day of the month you are paid on." in response.data
        assert re.search(
            rb'id="day_of_month"[^>]*is-invalid', response.data,
        ), "the refusal must mark the monthly arm's own box"
        # The same accessibility contract ``render_field`` keeps: the box says
        # it is invalid and names the feedback that says why.
        assert re.search(
            rb'id="day_of_month"[^>]*aria-describedby="day_of_month-error '
            rb'day_of_month-help"[^>]*aria-invalid="true"', response.data,
        )
        assert b'id="day_of_month-error"' in response.data

    def test_a_blank_second_day_is_refused_on_the_second_box(
        self, bare_auth_client,
    ):
        """Twice a month chosen with one day typed: the second box."""
        response = self._generate(
            bare_auth_client, cadence_kind="semi_monthly",
            first_day_of_month="1", second_day_of_month="",
        )
        assert response.status_code == 422
        assert b"Enter the second of your two paydays." in response.data
        assert re.search(
            rb'id="second_day_of_month"[^>]*is-invalid', response.data,
        )

    def test_two_equal_days_are_refused_by_the_write_doors_own_sentence(
        self, bare_auth_client,
    ):
        """The pair rule is asked, not restated: the service's words, second box."""
        response = self._generate(
            bare_auth_client, **cadence_form_values(SemiMonthly((15, 15))),
        )
        assert response.status_code == 422
        assert b"Twice a month needs two different days; got 15 twice." in (
            response.data
        )
        assert re.search(
            rb'id="second_day_of_month"[^>]*is-invalid', response.data,
        )

    def test_a_pair_that_collapses_in_february_is_refused(
        self, bare_auth_client,
    ):
        """The 28th and the 30th both land on February's last day (R-PC79)."""
        response = self._generate(
            bare_auth_client, start_date="2026-06-28",
            **cadence_form_values(SemiMonthly((28, 30))),
        )
        assert response.status_code == 422
        assert b"must be day 27 or before; got 28 and 30" in response.data

    def test_a_first_payday_off_the_grid_is_refused_on_the_payday_box(
        self, bare_auth_client,
    ):
        """Monthly on the 15th, first payday typed as the 10th: the DATE box.

        The grid question reaches the schema now (plan step
        ``pay_calendar:C17-d-3``), so it renders under "First scheduled
        payday" as a field error rather than falling through to the route's
        ``except ValidationError`` -- which also renders under that box, but
        by way of a handler whose enumeration says only the repopulation can
        reach it.
        """
        response = self._generate(
            bare_auth_client, start_date="2026-06-10",
            **cadence_form_values(Monthly(15)),
        )
        assert response.status_code == 422
        assert b"2026-06-10 is not a day you are paid on when paid monthly " in (
            response.data
        )
        assert b'id="start_date-error"' in response.data, (
            "the refusal must be attributed to the payday control"
        )

    def test_a_pair_too_close_for_a_moving_convention_is_refused_on_shift(
        self, bare_auth_client,
    ):
        """The 1st and the last: one day apart across February, so ``prior`` is refused.

        The floor is asked of the era's SHORTEST GAP (**R-PC79**): 1 for
        this pair, below the collision floor, and the control at fault is the
        convention -- the same attribution the fortnight's two-day case has.
        """
        response = self._generate(
            bare_auth_client, start_date="2026-06-01",
            **cadence_form_values(SemiMonthly((1, 31))),
            shift=shift_form_value(BusinessDayShiftEnum.PRIOR),
        )
        assert response.status_code == 422
        assert b"got 1 (paid twice a month on days 1 and 31)" in response.data
        assert b'id="shift-error"' in response.data

    def test_an_unknown_token_is_refused_on_the_kind(self, bare_auth_client):
        """A direct POST naming no kind: the kind control's own sentence."""
        response = self._generate(bare_auth_client, cadence_kind="weekly")
        assert response.status_code == 422
        assert b"Choose how often you are paid." in response.data

    def test_regenerate_names_the_payday_box_for_an_off_grid_day(
        self, app, db, auth_client, seed_user,
    ):
        """The manage door flashes ``new_start_date:`` for the grid question."""
        with app.app_context():
            _spanning_periods(db.session, seed_user)
            response = auth_client.post("/pay-periods/regenerate", data={
                "new_start_date": "2026-06-25",
                "num_periods": "3",
                **cadence_form_values(Monthly(20)),
                "shift": shift_form_value(),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"new_start_date: A first payday of 2026-06-25 is not a day" in (
                response.data
            )
            db.session.expire_all()
            assert _stored_cadence(seed_user["user"].id) == FixedDays(14)

    def test_regenerate_and_reset_require_the_kind(
        self, app, db, auth_client, seed_user,
    ):
        """A door that corrects a rhythm may not silently restate its kind.

        Both manage doors refuse a payload with no ``cadence_kind`` rather
        than reading it as the fortnight, exactly as they refuse a missing
        convention; the generate door and sign-up default it instead.
        """
        with app.app_context():
            _spanning_periods(db.session, seed_user)
            for door, extra in (
                ("/pay-periods/regenerate", {}),
                ("/pay-periods/reset", {"confirm": "true"}),
            ):
                body = {
                    "new_start_date": "2026-06-19",
                    "num_periods": "3",
                    **cadence_form_values(),
                    "shift": shift_form_value(),
                    **extra,
                }
                del body["cadence_kind"]
                response = auth_client.post(
                    door, data=body, follow_redirects=True,
                )
                assert response.status_code == 200
                assert b"cadence_kind: Missing data for required field." in (
                    response.data
                ), door
            db.session.expire_all()
            assert _stored_cadence(seed_user["user"].id) == FixedDays(14)


class TestTheRegenerateConfirmBannerRoundTripsAMonthKind:
    """The discard-confirm banner re-posts a month kind's arm, not a day count.

    The banner echoes the params the 422 handed it as hidden inputs, and the
    regenerate schema requires the kind: a params dict that still spelled
    ``cadence_days`` would refuse every discard-confirm of a monthly owner
    with "cadence_kind: Missing data" while the fortnight's twin case stayed
    green.  ``rhythm_to_wire`` is what the route echoes, and this is the one
    case that grades it through the browser's round trip.
    """

    def test_the_hidden_inputs_carry_the_chosen_arm_and_re_post_cleanly(
        self, app, db, auth_client, seed_user,
    ):
        """A monthly regenerate over a hand-entered row: 422, echo, confirm, stored."""
        with app.app_context():
            _spanning_periods(db.session, seed_user)
            add_txn(db.session, seed_user, all_periods(
                seed_user["user"].id,
            )[-1], "Cash", "50.00")
            db.session.commit()

            confirm = auth_client.post("/pay-periods/regenerate", data={
                "new_start_date": "2026-06-25",
                "num_periods": "3",
                **cadence_form_values(Monthly(25)),
                "shift": shift_form_value(BusinessDayShiftEnum.PRIOR),
            })
            assert confirm.status_code == 422

            echoed = dict(re.findall(
                rb'<input type="hidden" name="([a-z_]+)" value="([^"]*)"',
                confirm.data,
            ))
            assert echoed[b"cadence_kind"] == b"monthly"
            assert echoed[b"day_of_month"] == b"25"
            assert b"cadence_days" not in echoed, (
                "the banner echoed an arm the owner did not choose"
            )
            assert echoed[b"shift"] == shift_form_value(
                BusinessDayShiftEnum.PRIOR,
            ).encode()

            resp = auth_client.post("/pay-periods/regenerate", data={
                key.decode(): value.decode() for key, value in echoed.items()
            } | {"confirm_discard": "true"}, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Rebuilt the schedule" in resp.data
            db.session.expire_all()
            stored = pay_schedule_service.resolve_schedule(
                seed_user["user"].id,
            ).rhythm
            assert stored.cadence == Monthly(25)
            assert stored.shift is BusinessDayShiftEnum.PRIOR
