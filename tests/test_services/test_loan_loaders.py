"""Tests for the loan data-loader leaf (anchor-fact synthesis).

:mod:`app.services.loan_loaders` is the leaf layer every loan consumer loads
through.  These tests pin its C11 centerpiece --
:func:`~app.services.loan_loaders.load_loan_anchor_facts` -- the ONE anchor
loader the genesis posting walk and every resolver-input builder share: the
origination anchor is SYNTHESIZED from the immutable :class:`LoanParams` and is
ALWAYS the opening (step C1; the origination :class:`LoanAnchorEvent` write is
retired), while stored ``tracking_start`` and ``user_trueup`` events load as
non-opening balance ASSERTIONS, and legacy stored origination rows are ignored
(they were always verbatim copies of the params, verified on production
data).

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date, timezone
from decimal import Decimal

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.extensions import db as _db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.services import loan_loaders, loan_resolver
from tests._test_helpers import (
    create_loan_account,
    insert_tracking_start_event,
    insert_trueup_event,
)

_PRINCIPAL = Decimal("250000.00")
_ORIGINATION = date(2025, 1, 1)


def _params(loan):
    """Return the loan account's :class:`LoanParams` row."""
    return _db.session.query(LoanParams).filter_by(account_id=loan.id).one()


class TestLoadLoanAnchorFacts:
    """The anchor facts: synthesized origination + stored true-ups, one loader."""

    def test_origination_fact_is_synthesized_from_params(
        self, app, db, seed_user,
    ):
        """The opening fact mirrors the immutable params, never a stored row.

        A fresh loan's fact list is exactly one OPENING fact carrying the
        params' origination date and original principal, dated with the
        earliest possible UTC instant so any same-day true-up outranks it
        in the ``(anchor_date, created_at)`` latest-anchor ordering.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            facts = loan_loaders.load_loan_anchor_facts(_params(loan))

            (opening,) = [fact for fact in facts if fact.is_opening]
            assert opening.account_id == loan.id
            assert opening.anchor_date == _ORIGINATION
            assert opening.anchor_balance == _PRINCIPAL
            assert opening.created_at.tzinfo is timezone.utc

    def test_legacy_origination_rows_are_ignored(self, app, db, seed_user):
        """A stored origination event never becomes a second opening fact.

        Real production data still carries the pre-retirement origination
        :class:`LoanAnchorEvent` rows (the Mortgage's 2018-12-01, the Van
        Loan's 2023-02-14): the write is retired but the rows were never
        migrated.  ``load_loan_anchor_facts`` loads only the USER_TRUEUP and
        TRACKING_START sources, so an ORIGINATION-source row never enters --
        the opening is synthesized from the params and IGNORES that row,
        exactly one opening fact, value-identical to the synthesis, so
        pre-retirement and post-retirement loans walk the same facts.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            # Seed a legacy ORIGINATION-source row (the retired write), as the
            # real loans still carry; the loader must not read it.
            db.session.add(LoanAnchorEvent(
                account_id=loan.id,
                anchor_date=_ORIGINATION,
                anchor_balance=_PRINCIPAL,
                source_id=ref_cache.loan_anchor_source_id(
                    LoanAnchorSourceEnum.ORIGINATION,
                ),
            ))
            db.session.commit()
            assert (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=loan.id)
                .count()
            ) == 1

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assert len(facts) == 1
            assert facts[0].is_opening is True
            assert facts[0].is_tracking_start is False

    def test_trueup_events_become_non_opening_facts(self, app, db, seed_user):
        """Each stored user true-up loads as a non-opening fact with its values.

        A $200,000 true-up on 2026-02-15 loads beside the synthesized
        opening: two facts, the true-up carrying the stored date / balance
        and ``is_opening=False`` (it books the TRUEUP kinds, never a second
        opening).
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_trueup_event(
                _params(loan), Decimal("200000.00"), date(2026, 2, 15),
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assert len(facts) == 2
            (trueup,) = [fact for fact in facts if not fact.is_opening]
            assert trueup.anchor_date == date(2026, 2, 15)
            assert trueup.anchor_balance == Decimal("200000.00")

    def test_tracking_start_is_a_non_opening_assertion(self, app, db, seed_user):
        """A ``tracking_start`` loads as an assertion, NOT the opening (step C1).

        For a mid-life-imported loan the opening stays the synthesized
        ORIGINATION; the ``tracking_start`` is an ``is_opening=False`` balance
        assertion carrying ``is_tracking_start=True`` for the display label.  A
        $180,000 tracking-start as of 2025-06-01 on a loan that originated at
        $250,000 on 2025-01-01 yields TWO facts: the origination opening
        (250000 / 2025-01-01, ``is_tracking_start=False``) and the tracking-start
        assertion (180000 / 2025-06-01, ``is_opening=False``,
        ``is_tracking_start=True``).
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_tracking_start_event(
                _params(loan), Decimal("180000.00"), date(2025, 6, 1),
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assert len(facts) == 2
            (opening,) = [fact for fact in facts if fact.is_opening]
            assert opening.anchor_date == _ORIGINATION
            assert opening.anchor_balance == _PRINCIPAL
            assert opening.is_tracking_start is False
            (tracking,) = [fact for fact in facts if not fact.is_opening]
            assert tracking.anchor_date == date(2025, 6, 1)
            assert tracking.anchor_balance == Decimal("180000.00")
            assert tracking.is_opening is False
            assert tracking.is_tracking_start is True

    def test_same_day_tracking_start_correction_latest_created_governs(
        self, app, db, seed_user,
    ):
        """Two same-day tracking-starts (a correction): latest created governs.

        The table is append-only, so a correction is another ``tracking_start``
        row.  Both load as non-opening assertions (``is_tracking_start=True``);
        on a shared date the walk applies them in ``created_at`` order, so the
        LATEST-created is the last reset and governs -- the exact model the real
        Van Loan carries (two 2026-04-11 tracking-starts).  A later-committed
        $180,000 correction on 2025-05-01 supersedes the earlier
        $185,000/2025-05-01 assertion, and
        :func:`~app.services.loan_resolver.select_latest_anchor` (keyed on
        ``(anchor_date, created_at)``) picks it.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_tracking_start_event(
                _params(loan), Decimal("185000.00"), date(2025, 5, 1),
            )
            db.session.commit()
            # A later-committed correction on the SAME date (distinct balance, so
            # the same-day unique index permits it); its created_at is strictly
            # later, so it is the authoritative assertion.
            insert_tracking_start_event(
                _params(loan), Decimal("180000.00"), date(2025, 5, 1),
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assertions = [fact for fact in facts if not fact.is_opening]
            assert len(assertions) == 2
            assert all(fact.is_tracking_start for fact in assertions)
            latest = loan_resolver.select_latest_anchor(facts)
            assert latest.is_tracking_start is True
            assert latest.anchor_balance == Decimal("180000.00")
            assert latest.anchor_date == date(2025, 5, 1)

    def test_same_day_trueup_outranks_the_synthesized_opening(
        self, app, db, seed_user,
    ):
        """A true-up dated ON the origination date is the latest anchor.

        The resolver picks its replay anchor by ``(anchor_date, created_at)``
        DESC.  The synthesized opening carries the earliest possible instant,
        so a true-up asserted on the very origination date still wins the
        tie -- exactly as the stored origination row (created at setup,
        before any true-up) behaved.  A $240,000 same-day true-up must be
        the selected anchor, not the $250,000 opening.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_trueup_event(
                _params(loan), Decimal("240000.00"), _ORIGINATION,
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            latest = loan_resolver.select_latest_anchor(facts)
            assert latest.is_opening is False
            assert latest.anchor_balance == Decimal("240000.00")
