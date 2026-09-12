"""The ERA rule at the writer: which batches mint, retire or continue an era.

Plan step ``pay_calendar:C17-a`` (ruling **R-PC58**).  ``record_paydays``
decides through ``pay_schedule_service.era_to_mint`` whether the batch states
a new era, and ``_apply`` retires the eras a mint supersedes.  The rule is
graded in ``test_pay_schedule_service.TestTheEraRule`` on values; these cases
drive the WRITER and read ``budget.pay_eras`` back, so a rule that is right and
a writer that ignores it fail here.

Every case names the eras it expects as ``(effective_from, cadence_days)``
pairs, ascending, read by SQL rather than through the model: the table is the
subject.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import BusinessDayShiftEnum
from app.exceptions import ValidationError
from app.services import (
    pay_period_admin,
    pay_period_write,
    pay_schedule_service,
)
from app.services.pay_calendar import calendar_for
from app.utils.business_days import shortest_collision_free_cadence
from tests._test_helpers import all_periods, rhythm_of


def _eras(session, user_id):
    """Return ``[(effective_from, cadence_days)]`` for *user_id*, ascending."""
    return [
        (row[0], row[1]) for row in session.execute(text(
            "SELECT effective_from, cadence_days FROM budget.pay_eras "
            " WHERE user_id = :uid ORDER BY effective_from"
        ), {"uid": user_id})
    ]


def _paydays(session, user_id):
    """Return the owner's recorded paydays, ascending."""
    return [
        row[0] for row in session.execute(text(
            "SELECT start_date FROM budget.pay_periods "
            " WHERE user_id = :uid ORDER BY start_date"
        ), {"uid": user_id})
    ]


def _all_period_ids(user_id):
    """Every period id the owner holds, for a whole-schedule replacement."""
    return pay_period_write.owner_period_ids(user_id)


class TestABatchMintsAnEraWhenItStatesOne:
    """The three arms of the rule, each through the writer."""

    def test_a_first_batch_mints_an_era_at_its_first_payday(
        self, app, db, bare_user,
    ):
        """A first schedule is one era, phased on the batch's own first payday."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=3, rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert _eras(db.session, user_id) == [(date(2026, 1, 2), 14)]
            assert pay_schedule_service.get_schedule(user_id) is not None

    def test_a_cadence_corrected_going_forward_KEEPS_the_past_era(
        self, app, db, bare_user,
    ):
        """Ledger row **N-492**'s write half: the old rhythm is not overwritten.

        The exact state ``test_c4c_pay_period_is_one_fact`` builds -- paydays
        14 and 35 days apart -- held one stored cadence of 7 before this step
        and describes the first two paydays as 7-day ones.  Now the 14-day
        era survives beside the 7-day one, and each payday's era is the one
        covering its day.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 20),
                num_periods=1, rhythm=rhythm_of(7),
            )
            db.session.commit()

            assert _eras(db.session, user_id) == [
                (date(2026, 1, 2), 14), (date(2026, 2, 20), 7),
            ]
            assert pay_schedule_service.resolve_cadence(user_id) == 7

    def test_a_phase_corrected_going_forward_mints_an_era_at_the_new_day(
        self, app, db, bare_user,
    ):
        """Same cadence, first payday OFF the covering grid: a new era.

        ``regenerate``'s shape -- the tail from 2026-01-30 is retired and
        rebuilt from 2026-02-03 at the same 14 days.  2026-02-03 is 32 days
        past the era's day, not a multiple of 14, so the batch mints from it;
        the retired 01-30 paycheck's era (the first) stays for the paydays it
        still describes.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=3, rhythm=rhythm_of(14),
            )
            db.session.commit()
            doomed = {
                period_id
                for period_id, payday in pay_period_write._owner_paydays(  # pylint: disable=protected-access
                    user_id,
                )
                if payday >= date(2026, 1, 30)
            }

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 3),
                num_periods=2, rhythm=rhythm_of(14),
                replacing=pay_period_write.SpanReplacement(retiring_ids=doomed),
            )
            db.session.commit()

            assert _paydays(db.session, user_id) == [
                date(2026, 1, 2), date(2026, 1, 16),
                date(2026, 2, 3), date(2026, 2, 17),
            ]
            assert _eras(db.session, user_id) == [
                (date(2026, 1, 2), 14), (date(2026, 2, 3), 14),
            ]


    def test_a_row_the_session_already_holds_answers_the_NEW_era(
        self, app, db, bare_user,
    ):
        """``PaySchedule.eras`` is view-only, so ``_apply`` expires the session after a mint.

        A request that loaded the schedule row before a mint -- the generate
        route reads it to dispatch, then records -- would otherwise keep the
        eras the row had BEFORE: a view-only relationship gets no event from
        the insert, and a joined load does not replace a collection the
        identity map already holds.  **What makes this pass is the
        ``expire_all`` in ``pay_period_write._apply``**, measured by deleting
        its mint clause: the second read then answers ``[14]``.  Graded
        through the row the session ALREADY holds, which is the instance
        ``get_schedule`` returns again.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            held = pay_schedule_service.get_schedule(user_id)
            assert [e.cadence_days for e in held.eras] == [14]

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 20),
                num_periods=1, rhythm=rhythm_of(7),
            )

            again = pay_schedule_service.get_schedule(user_id)
            assert again is held, "the identity map hands back the same row"
            assert [e.cadence_days for e in again.eras] == [14, 7]
            assert pay_schedule_service.resolve_cadence(user_id) == 7


class TestAContinuingBatchMintsNothing:
    """The negative arm: extend and the top-up write no era."""

    def test_an_extend_leaves_the_one_era_standing(self, app, db, bare_user):
        """The extend's first payday is on the era's grid at its rhythm."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.commit()

            pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()

            assert _eras(db.session, user_id) == [(date(2026, 1, 2), 14)]
            assert _paydays(db.session, user_id)[-1] == date(2026, 2, 27)

    def test_a_batch_naming_only_existing_paydays_writes_no_era(
        self, app, db, bare_user,
    ):
        """Finding **P12**'s shape: a batch that creates nothing states nothing.

        Even at a NEW cadence -- the rhythm rides with the paydays it spaces,
        and there are none.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(14),
            )
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=1, rhythm=rhythm_of(30),
            )
            db.session.commit()

            assert created == []
            assert _eras(db.session, user_id) == [(date(2026, 1, 2), 14)]

    def test_a_continuing_batch_is_NOT_re_judged_against_the_collision_floor(
        self, app, db, bare_user,
    ):
        """Ledger row **N-494**, closed: an illegal STORED pair no longer refuses an extend.

        The pair is made illegal underneath the owner by SQL -- a cadence of
        3 under ``prior`` sits below the floor of 4 -- which is what a later
        holiday-set change would do to a pair the write door once accepted
        (ledger row **N-493**).  Before this step the extend handed that pair
        back through the schedule upsert and was refused, on a read path with
        no handler; now the extend continues the era and records the grid's
        next payday.  Hand-computed on the 3-day grid from 08-04 (plan step
        ``C17-b-2``): the last record 09-01 stands for the grid's 08-31, paid
        a day late, so the next planned payday is 09-03, an open Thursday,
        and that is the day recorded.  *It was 09-04 -- the nominal 09-06
        displaced onto the Friday before the Labor Day weekend -- while the
        horizon stepped one cadence from the recorded 09-01.*

        **The control is the STATING batch below**: the same pair, stated by
        a batch that would mint, is still refused with the floor's message --
        so this case cannot pass by the refusal having been deleted.
        """
        user_id = bare_user["user"].id
        floor = shortest_collision_free_cadence()
        assert floor > 3, "the case needs 3 to be BELOW the floor"
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 8, 4),
                num_periods=3, rhythm=rhythm_of(14),
            )
            db.session.commit()
            db.session.execute(text(
                "UPDATE budget.pay_eras SET cadence_days = 3, shift_id = :prior "
                " WHERE user_id = :uid"
            ), {
                "prior": ref_cache.business_day_shift_id(
                    BusinessDayShiftEnum.PRIOR,
                ),
                "uid": user_id,
            })
            db.session.commit()

            appended = pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert [p.start_date for p in appended] == [date(2026, 9, 3)]
            assert _eras(db.session, user_id) == [(date(2026, 8, 4), 3)]

            # 2026-09-10 is off the 3-day grid through 08-04 (37 days), and
            # an open Thursday no recorded payday holds, so the batch would
            # record a day and mint -- which is where the pairing is judged.
            with pytest.raises(ValidationError, match=f"at least {floor}"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 9, 10),
                    num_periods=1,
                    rhythm=rhythm_of(3, BusinessDayShiftEnum.PRIOR),
                )
            db.session.rollback()


class TestAMintRetiresWhatItSupersedes:
    """Minting from day X retires every era from X on; a wipe retires them all."""

    def test_a_rebuild_from_an_earlier_day_retires_the_later_era(
        self, app, db, bare_user,
    ):
        """Eras from 01-02 (14) and 02-20 (7); a rebuild from 02-13 at 30 supersedes 02-20."""
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 20),
                num_periods=2, rhythm=rhythm_of(7),
            )
            db.session.commit()
            doomed = {
                period_id
                for period_id, payday in pay_period_write._owner_paydays(  # pylint: disable=protected-access
                    user_id,
                )
                if payday >= date(2026, 2, 20)
            }

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 13),
                num_periods=1, rhythm=rhythm_of(30),
                replacing=pay_period_write.SpanReplacement(retiring_ids=doomed),
            )
            db.session.commit()

            assert _eras(db.session, user_id) == [
                (date(2026, 1, 2), 14), (date(2026, 2, 13), 30),
            ]

    def test_a_rebuild_CONTINUING_the_earlier_era_retires_the_later_one(
        self, app, db, bare_user,
    ):
        """An adversarial review of C17-a built this regenerate, and it was wrong.

        Eras from 01-02 (14) and 02-20 (7).  A regenerate from 01-30 at 14
        days, retiring the 7-day tail, CONTINUES the first era -- same rhythm,
        on its grid -- so nothing is minted; the rule that retired only on a
        mint left the 7-day era standing as the LATEST, and every reader then
        derived a fortnightly owner weekly.  The retirement is keyed on the
        record instead: every era past the last surviving payday goes,
        whether or not the batch mints.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 20),
                num_periods=2, rhythm=rhythm_of(7),
            )
            db.session.commit()
            doomed = {
                period_id
                for period_id, payday in pay_period_write._owner_paydays(  # pylint: disable=protected-access
                    user_id,
                )
                if payday >= date(2026, 2, 20)
            }

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 30),
                num_periods=3, rhythm=rhythm_of(14),
                replacing=pay_period_write.SpanReplacement(retiring_ids=doomed),
            )
            db.session.commit()

            assert _paydays(db.session, user_id) == [
                date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
                date(2026, 2, 13), date(2026, 2, 27),
            ]
            assert _eras(db.session, user_id) == [(date(2026, 1, 2), 14)]
            assert pay_schedule_service.resolve_cadence(user_id) == 14

    def test_a_whole_schedule_replacement_leaves_ONE_era(
        self, app, db, bare_user,
    ):
        """``reset``'s shape: every payday goes, so every era goes, and one is minted.

        Even where the new batch's rhythm and grid match an era already
        held -- the same 14 days from a day on the old grid -- because
        nobody was paid on the retired eras any more, and an era is *how I
        have been paid since*.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 20),
                num_periods=1, rhythm=rhythm_of(7),
            )
            db.session.commit()

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2025, 12, 5),
                num_periods=3, rhythm=rhythm_of(14),
                replacing=pay_period_write.SpanReplacement(
                    retiring_ids=_all_period_ids(user_id),
                ),
            )
            db.session.commit()

            assert _eras(db.session, user_id) == [(date(2025, 12, 5), 14)]
            assert _paydays(db.session, user_id) == [
                date(2025, 12, 5), date(2025, 12, 19), date(2026, 1, 2),
            ]

    def test_a_truncate_retires_NO_era(self, app, db, bare_user):
        """Retiring a tail shortens the record and leaves the declared rhythm.

        The 7-day era's paydays all go; the era stays, so the next extend
        continues the rhythm the owner stated rather than reverting to the
        one before it.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=2, rhythm=rhythm_of(14),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 20),
                num_periods=2, rhythm=rhythm_of(7),
            )
            db.session.commit()
            doomed = {
                period_id
                for period_id, payday in pay_period_write._owner_paydays(  # pylint: disable=protected-access
                    user_id,
                )
                if payday >= date(2026, 2, 20)
            }

            assert pay_period_write.retire_paydays(user_id, doomed) == 2
            db.session.commit()

            assert _eras(db.session, user_id) == [
                (date(2026, 1, 2), 14), (date(2026, 2, 20), 7),
            ]
            assert pay_schedule_service.resolve_cadence(user_id) == 7


class TestTheEraRuleIsKeyedOnTheErasFirstPayday:
    """Ledger row **PC-510**, closed at plan step ``C17-b-2``: the rule reads CASH days.

    ``eras_describing`` and ``retire_eras`` compared an era's nominal
    ``effective_from`` with the RECORD's cash paydays.  An era minted on a
    nominal closed day under ``prior`` pays its first paycheck BEFORE its own
    ``effective_from``, so the moment that paycheck was its only one the rule
    said the era described nothing and the next batch retired and re-minted
    it; under ``next`` the mirror kept an era whose only paycheck a truncate
    had removed.  Both halves are measured through the doors here.
    """

    def test_an_era_minted_on_a_holiday_under_prior_survives_the_next_extend(
        self, app, db, bare_user,
    ):
        """Thanksgiving 2030 under ``prior``: minted 11-28, paid 11-27, and KEPT.

        Measured before the fix: eras ``[10-26 / 7 days, 11-28 / 14 days]``
        with the record ending 11-27 became ``[10-26 / 7 days, 12-12 / 14
        days]`` after one extend -- the right day recorded, the era retired
        and re-minted a fortnight late, and the 11-27 paycheck described by
        the weekly era thereafter.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 10, 26),
                num_periods=3, rhythm=rhythm_of(7),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 11, 28),
                num_periods=1, rhythm=rhythm_of(14, BusinessDayShiftEnum.PRIOR),
            )
            db.session.commit()
            assert _paydays(db.session, user_id)[-1] == date(2030, 11, 27)
            assert _eras(db.session, user_id) == [
                (date(2030, 10, 26), 7), (date(2030, 11, 28), 14),
            ]

            appended = pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert [p.start_date for p in appended] == [date(2030, 12, 12)]
            assert _eras(db.session, user_id) == [
                (date(2030, 10, 26), 7), (date(2030, 11, 28), 14),
            ]

    def test_a_rebuild_keeps_the_era_whose_only_paycheck_it_leaves_standing(
        self, app, db, bare_user,
    ):
        """The reviewer's variant: a 28-day ``prior`` era whose one paycheck is its first.

        Reset from Sunday 2026-02-08 at 28 days under ``prior`` records
        Friday 02-06; a rebuild from 03-27 at 26 days under ``next`` (its
        first payday 03-27 is a Friday) used to see ``max(surviving) = 02-06
        < effective_from 02-08`` and delete the 28-day era, leaving the
        02-06 paycheck described by the 26-day one -- its derived end and
        the backward rhythm below it both moved.  The era stands now.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 8),
                num_periods=1, rhythm=rhythm_of(28, BusinessDayShiftEnum.PRIOR),
            )
            db.session.commit()
            assert _paydays(db.session, user_id) == [date(2026, 2, 6)]

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 3, 27),
                num_periods=1, rhythm=rhythm_of(26, BusinessDayShiftEnum.NEXT),
            )
            db.session.commit()

            assert _eras(db.session, user_id) == [
                (date(2026, 2, 8), 28), (date(2026, 3, 27), 26),
            ]
            calendar = calendar_for(user_id)
            assert calendar.periods[0].end_date == date(2026, 3, 26)

    def test_an_era_whose_only_paycheck_was_truncated_is_retired_by_the_next_batch(
        self, app, db, bare_user,
    ):
        """The ``next`` mirror: nominal 01-17 paid 01-20, truncated away, then a rebuild at 01-20.

        Compared on the nominal day the 01-17 era survived (01-17 is below
        the surviving 01-18) beside a new era whose first payday is the same
        01-20 -- two eras paying the same day, which the calendar refuses.
        Compared on its first payday it describes nothing that stands and is
        retired before the mint.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 14),
                num_periods=3, rhythm=rhythm_of(2),
            )
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 17),
                num_periods=1, rhythm=rhythm_of(14, BusinessDayShiftEnum.NEXT),
            )
            db.session.commit()
            assert _paydays(db.session, user_id)[-1] == date(2026, 1, 20)
            pay_period_write.retire_paydays(user_id, {
                p.id for p in all_periods(user_id)
                if p.start_date == date(2026, 1, 20)
            })
            db.session.commit()
            assert _eras(db.session, user_id) == [
                (date(2026, 1, 14), 2), (date(2026, 1, 17), 14),
            ]

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 20),
                num_periods=2, rhythm=rhythm_of(7, BusinessDayShiftEnum.PRIOR),
            )
            db.session.commit()

            assert _eras(db.session, user_id) == [
                (date(2026, 1, 14), 2), (date(2026, 1, 20), 7),
            ]
            assert calendar_for(user_id).periods[-1].start_date == date(2026, 1, 27)

    def test_restating_an_eras_convention_from_its_own_day_is_refused_not_collided(
        self, app, db, bare_user,
    ):
        """A rebuild from an EXISTING era's day, every lower payday held, no longer 500s.

        Before plan step ``C17-b-2`` this batch -- record 01-02, 01-16, 01-30
        at 14 days under ``none``, then 4 periods from 01-02 at 14 days under
        ``prior`` -- added only 02-13, passed the floor, minted an era on
        01-02 while the 01-02 era stood, and collided on
        ``uq_pay_eras_user_effective_from``: an IntegrityError on a form
        post (found by the adversarial review of this step).  The floor
        bounds a minting batch at its era's first payday now, so the batch
        is refused with the floor's message: restating a rhythm from a day
        the record already holds is the era-mint door's question
        (``C17-c-2``, ruling R-PC64), and a rebuild that RETIRES the tail
        from that day replaces the era cleanly.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=3, rhythm=rhythm_of(14),
            )
            db.session.commit()

            with pytest.raises(ValidationError, match="on or after 2026-02-13"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 1, 2),
                    num_periods=4,
                    rhythm=rhythm_of(14, BusinessDayShiftEnum.PRIOR),
                )
            db.session.rollback()
            assert _eras(db.session, user_id) == [(date(2026, 1, 2), 14)]

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2), num_periods=4,
                rhythm=rhythm_of(14, BusinessDayShiftEnum.PRIOR),
                replacing=pay_period_write.SpanReplacement(
                    retiring_ids={p.id for p in all_periods(user_id)},
                ),
            )
            db.session.commit()
            assert _paydays(db.session, user_id) == [
                date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
                date(2026, 2, 13),
            ]
            facts = pay_schedule_service.resolve_schedule(user_id)
            assert [(e.effective_from, e.rhythm.shift) for e in facts.eras] == [
                (date(2026, 1, 2), BusinessDayShiftEnum.PRIOR),
            ]
