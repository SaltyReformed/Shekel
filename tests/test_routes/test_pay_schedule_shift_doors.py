"""Every door that asks for a cadence also asks for the payday convention.

Plan step ``pay_calendar:C14-b``, ruling **R-PC56**.  That ruling is ONE claim
about FOUR surfaces -- registration, the generate card, regenerate and reset --
so it is graded in one file rather than scattered across four.  A fifth door
added without the question, or one of these four losing it, fails here.

**Each case drives HTTP rather than the service**, because the path the ruling
is about is the browser's: the control is rendered from the shared
``PAYDAY_SHIFT_OPTIONS``, submitted as a ``ref.business_day_shifts`` id
alongside every other control the form carries, deserialized to an enum member
by
:class:`~app.schemas.validation.pay_periods.BusinessDayShiftField`, and written
beside the cadence in one statement.  A break anywhere in that chain reads to
an owner as "I answered and the app ignored me", which is exactly the failure
**N-398** records payroll making in the other direction.

Nothing here asserts that a payday MOVED, and that is deliberate: C14-b lands
the column and the doors with behaviour OFF.  ``C14-e`` is the leaf that
applies the convention at the producer, and it is the one that moves money.
"""
import re
from datetime import timedelta

import pytest

from app import ref_cache
from app.enums import BusinessDayShiftEnum
from app.models.user import User
from app.services import pay_schedule_service
from app.utils.dates import display_today
from tests._test_helpers import (
    all_periods,
    rebuild_calendar,
    register_form_data,
    shift_form_value,
)


def _stored_shift(user_id):
    """Return the convention ``budget.pay_schedule`` holds for *user_id*.

    Read through :func:`~app.services.pay_schedule_service.resolve_schedule`
    since plan step ``pay_calendar:C14-e-1``, which deleted the scalar
    ``resolve_shift`` and its duplicate query once the pay CALENDAR acquired a
    reader for the convention.  Same column, same row, one read -- and the
    pair rather than the half, which is what the doors below persist.
    """
    return pay_schedule_service.resolve_schedule(user_id).rhythm.shift


class TestTheControlIsRenderedOnAllFourDoors:
    """The question is ASKED, which is the half a POST test cannot see.

    A route that accepted the field while no form rendered it would pass every
    persistence case below and still leave the owner unable to answer -- the
    defect finding **P29** records in its own domain, where the extend card
    rendered no cadence control and the schema accepted one anyway.
    """

    def test_the_registration_form_offers_all_three_conventions(self, client):
        """Sign-up asks before the owner has any schedule to correct."""
        page = client.get("/register").data
        assert b'name="shift"' in page
        for member in BusinessDayShiftEnum:
            assert f'value="{ref_cache.business_day_shift_id(member)}"'.encode(
            ) in page

    def test_the_standalone_generate_page_offers_them(self, bare_auth_client):
        """``pay_periods/generate.html`` renders the shared partial's control.

        Reached by POSTing a payload the schema refuses, because a GET of
        ``/pay-periods/generate`` redirects to the settings dashboard -- the
        standalone template is what the 422 re-render answers with.  That is
        the surface worth grading here: it carries NO ``pp_schedule`` in its
        context, so it exercises the fallback arm of the template's
        ``selected_value``, which is the arm a first-time owner meets.

        *An earlier version of this docstring said a bare truth test would
        raise ``UndefinedError`` here.  It would not: this app does not
        configure ``StrictUndefined``, so Jinja's default undefined is simply
        falsy.  What the arm actually grades is that the fallback renders at
        all on a page carrying no ``pp_schedule``.*
        """
        response = bare_auth_client.post("/pay-periods/generate", data={
            "start_date": "not-a-date",
            "num_periods": "3",
            "cadence_days": "14",
            "shift": shift_form_value(),
        })

        assert response.status_code == 422
        assert b'name="shift"' in response.data

    def test_an_owner_with_a_rhythm_is_offered_it_on_regenerate_and_reset(
        self, auth_client,
    ):
        """Generate, regenerate and reset each carry the control.

        Counted EXACTLY, and an adversarial review of 2026-09-05 is why: this
        asserted ``>= 2`` on a page that renders THREE -- the settings section
        includes ``_pay_periods_form.html`` (generate) as well as
        ``_pay_periods_manage.html`` (regenerate and reset) -- so a form that
        lost its control would leave the count at 2 and the case green.  That
        is precisely the hole this docstring said counting exists to close,
        open in the assertion below it.

        The page holds seven POST forms and only these three state a rhythm,
        which is the same distinction R-PC56's own first form got wrong when
        it named two doors and one of them was not one.

        **The three are no longer on ONE page, and that is plan step
        ``pay_calendar:C14-f``** (ruling **R-PC63**).  The generate card and
        the manage card became EXCLUSIVE: an owner who holds no paydays is
        offered generate and nothing else, and an owner who holds some is
        offered regenerate and reset and never generate -- because generate is
        where they could restate a phase they already had, which is ledger row
        **P80**.  So the census splits across two owners, 2 here and 1 in the
        case below, and each half stays counted EXACTLY for the reason above.

        **It is two CASES and not one with two clients**: ``auth_client`` and
        ``bare_auth_client`` both wrap the same ``client`` fixture and log in
        over each other, so a case requesting both gets ONE session and
        silently measures one owner twice.  A first draft of this pair did
        exactly that and read 2 where it asserted 1.
        """
        continuing = auth_client.get("/settings?section=pay-periods").data
        # Regenerate and reset -- and NOT generate, which is the change.
        assert continuing.count(b'name="shift"') == 2
        assert b"Generate Pay Periods" not in continuing

    def test_an_owner_with_no_rhythm_is_offered_it_on_generate(
        self, bare_auth_client,
    ):
        """The establishing half of the same census (plan step C14-f).

        An owner holding no paydays is offered the generate card and no manage
        card, so the control appears exactly once.  Counted exactly for the
        reason the case above gives: a ``>=`` would let the card lose its
        control and stay green.
        """
        establishing = bare_auth_client.get("/settings?section=pay-periods").data
        assert establishing.count(b'name="shift"') == 1
        assert b"Generate Pay Periods" in establishing


class TestTheManageCardPreselectsTheLatestErasConvention:
    """The regenerate and reset selects open on the owner's STORED answer.

    Plan step ``pay_calendar:C17-a`` moved the convention from the schedule
    row to the era, and the three templates read ``pp_era.shift_id`` where
    they read ``pp_schedule.shift_id``; nothing graded the preselect before
    (the census above counts ``name="shift"`` only), which is how a re-pointed
    value could render every select back on ``none`` and pass.  Graded as the
    rendered ``<option ... selected>`` for the stored id, TWICE on the page
    (regenerate and reset), after a regenerate that stored ``prior``.
    """

    def test_a_stored_prior_is_the_selected_option_on_both_forms(
        self, app, auth_client, seed_user,
    ):
        """A regenerate at ``prior`` and the manage card then opens on ``prior``."""
        with app.app_context():
            # A record reaching today, so the rebuild opens inside the window
            # the writer allows -- from the plan's next payday (today + 7) up
            # to the one after it (plan step C17-c-2a, ruling R-PC67); over
            # the seeded 2024 opening payday alone it would skip two years.
            rebuild_calendar(
                seed_user["user"].id, display_today() - timedelta(days=7), 2, 14,
            )
            start = display_today() + timedelta(days=14)
            assert auth_client.post("/pay-periods/regenerate", data={
                "new_start_date": start.isoformat(),
                "num_periods": "3",
                "cadence_days": "14",
                "shift": shift_form_value(BusinessDayShiftEnum.PRIOR),
            }).status_code == 302
            prior_id = ref_cache.business_day_shift_id(BusinessDayShiftEnum.PRIOR)
            none_id = ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE)

            page = auth_client.get("/settings?section=pay-periods").data

            assert page.count(b'name="shift"') == 2
            assert len(re.findall(
                rb'<option value="%d"\s+selected>' % prior_id, page,
            )) == 2, "both selects must open on the stored convention"
            assert not re.findall(
                rb'<option value="%d"\s+selected>' % none_id, page,
            ), "and neither may snap back to none"


class TestEachDoorPersistsTheAnswer:
    """The chosen convention reaches ``budget.pay_schedule``."""

    def test_registration_stores_the_convention_the_form_states(
        self, app, db, client,
    ):
        """Sign-up's answer lands on the schedule row its paydays create."""
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="shift-register@example.com",
                display_name="Shift Register",
                last_payday=display_today().isoformat(),
                shift=shift_form_value(BusinessDayShiftEnum.PRIOR),
            ))

            assert response.status_code == 302
            user = db.session.query(User).filter_by(
                email="shift-register@example.com",
            ).one()
            assert _stored_shift(user.id) is BusinessDayShiftEnum.PRIOR

    def test_registration_defaults_to_none_when_nobody_answers(
        self, app, db, client,
    ):
        """The OFF state is what an unanswered sign-up leaves behind.

        R-PC56 refused defaulting to ``prior`` on the reasoning that real
        payroll usually pays early, because that states as fact what no owner
        was asked.  This is the case that would fail if a later change quietly
        reinstated it.
        """
        with app.app_context():
            body = register_form_data(
                email="shift-default@example.com",
                display_name="Shift Default",
                last_payday=display_today().isoformat(),
            )
            del body["shift"]
            response = client.post("/register", data=body)

            assert response.status_code == 302
            user = db.session.query(User).filter_by(
                email="shift-default@example.com",
            ).one()
            assert _stored_shift(user.id) is BusinessDayShiftEnum.NONE

    def test_generate_stores_the_convention(self, app, bare_auth_client,
                                            bare_user):
        """The first-schedule door writes both halves of the rhythm."""
        with app.app_context():
            response = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": display_today().isoformat(),
                "num_periods": "3",
                "cadence_days": "14",
                "shift": shift_form_value(BusinessDayShiftEnum.NEXT),
            })

            assert response.status_code == 302
            user_id = bare_user["user"].id
            assert _stored_shift(user_id) is BusinessDayShiftEnum.NEXT
            assert len(all_periods(user_id)) == 3

    def test_regenerate_stores_a_changed_convention(
        self, app, db, auth_client, seed_user,
    ):
        """Correcting the tail is also how an owner changes their answer."""
        user_id = seed_user["user"].id
        with app.app_context():
            # A record reaching today, for the reason the preselect case gives.
            rebuild_calendar(user_id, display_today() - timedelta(days=7), 2, 14)
            start = display_today() + timedelta(days=14)
            response = auth_client.post("/pay-periods/regenerate", data={
                "new_start_date": start.isoformat(),
                "num_periods": "3",
                "cadence_days": "14",
                "shift": shift_form_value(BusinessDayShiftEnum.PRIOR),
            })

            assert response.status_code == 302
            db.session.expire_all()
            assert _stored_shift(user_id) is BusinessDayShiftEnum.PRIOR

    def test_reset_stores_the_convention(
        self, app, db, auth_client, seed_user,
    ):
        """The first-time-setup correction states the whole rhythm again."""
        user_id = seed_user["user"].id
        with app.app_context():
            response = auth_client.post("/pay-periods/reset", data={
                "new_start_date": display_today().isoformat(),
                "num_periods": "4",
                "cadence_days": "14",
                "shift": shift_form_value(BusinessDayShiftEnum.NEXT),
                "confirm": "true",
            })

            assert response.status_code == 302
            db.session.expire_all()
            assert _stored_shift(user_id) is BusinessDayShiftEnum.NEXT


class TestADoorRefusesAPairNoCalendarCanDerive:
    """The joint rule renders as a form error, never as a 500.

    The refusal lives at the write door rather than in a CHECK constraint
    (ruling **R-PC59**, developer 2026-09-05), so these are the cases that
    prove an owner
    who picks an impossible pair is TOLD rather than shown a stack trace --
    which is what an ``IntegrityError`` escaping a constraint would give them.
    """

    def test_generate_refuses_a_displacing_convention_on_a_short_cadence(
        self, app, bare_auth_client, bare_user,
    ):
        """Cadence 2 with an early-pay convention is refused, not stored."""
        with app.app_context():
            response = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": display_today().isoformat(),
                "num_periods": "3",
                "cadence_days": "2",
                "shift": shift_form_value(BusinessDayShiftEnum.PRIOR),
            })

            assert response.status_code == 422
            # The error is rendered ON the ``shift`` control, which is the
            # whole claim: ``render_select`` emits ``id="shift-error"`` and
            # ``aria-invalid`` only for a field marshmallow attributed to
            # ``shift``.
            #
            # **A substring check on the message cannot grade this**, and an
            # adversarial review of 2026-09-05 caught this case doing exactly
            # that.  Delete ``validate_derivable_rhythm`` and the refusal falls
            # through to ``record_paydays``, the route renders it under
            # ``start_date`` -- and the message still contains "at least", so
            # the old assertion passed on the very deletion it existed to
            # catch.
            assert b'id="shift-error"' in response.data
            assert b'name="start_date"' in response.data
            assert b'id="start_date-error"' not in response.data
            user_id = bare_user["user"].id
            assert pay_schedule_service.get_schedule(user_id) is None
            assert all_periods(user_id) == []

    def test_the_same_short_cadence_is_accepted_with_no_convention(
        self, app, bare_auth_client, bare_user,
    ):
        """The control case: it is the PAIR that is refused, not the cadence.

        Without this the case above would pass just as well against a door
        that had simply raised the cadence floor for everybody -- which would
        re-open finding **P9**, whose ruling made a one-day schedule ordinary.
        """
        with app.app_context():
            response = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": display_today().isoformat(),
                "num_periods": "3",
                "cadence_days": "2",
                "shift": shift_form_value(BusinessDayShiftEnum.NONE),
            })

            assert response.status_code == 302
            user_id = bare_user["user"].id
            assert pay_schedule_service.resolve_cadence(user_id) == 2


class TestAnUnmodelledConventionIsRefusedAtTheSchema:
    """A submitted id the application does not model never reaches the column.

    ``fk_pay_schedule_shift_id`` would refuse it too, but as an
    ``IntegrityError`` 500 rather than as something the form can render --
    which is the same argument that put the cadence bound at the write door at
    plan step X-ad-a.
    """

    @pytest.mark.parametrize("bogus", ["0", "-1", "999999", "abc", ""])
    def test_a_bogus_shift_id_is_refused_and_stores_nothing(
        self, app, bare_auth_client, bare_user, bogus,
    ):
        """Every ill-formed spelling is refused as a 422, and stores nothing.

        **The status code is asserted, and an adversarial review of 2026-09-05
        is why.**  This checked only that no schedule row appeared -- and an
        ``IntegrityError`` 500 from ``fk_pay_schedule_shift_id``, which is the
        exact outcome this class says the schema prevents, also leaves no row.
        The case passed on its own failure mode.
        """
        with app.app_context():
            response = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": display_today().isoformat(),
                "num_periods": "3",
                "cadence_days": "14",
                "shift": bogus,
            })

            assert response.status_code == 422
            assert b'id="shift-error"' in response.data
            assert pay_schedule_service.get_schedule(
                bare_user["user"].id,
            ) is None
