"""Tests for the loan data-loader leaf (anchor-fact synthesis).

:mod:`app.services.loan_loaders` is the leaf layer every loan consumer loads
through.  These tests pin its C11 centerpiece --
:func:`~app.services.loan_loaders.load_loan_anchor_facts` -- the ONE anchor
loader the genesis posting walk and every resolver-input builder share: the
origination anchor is SYNTHESIZED from the immutable :class:`LoanParams`
(the origination :class:`LoanAnchorEvent` write is retired), user true-ups
are loaded as stored facts, and legacy stored origination rows are ignored
(they were always verbatim copies of the params, verified on production
data).

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date, timezone
from decimal import Decimal

from app.extensions import db as _db
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

        ``create_loan_account`` seeds a legacy origination
        :class:`LoanAnchorEvent` (the pre-retirement write).  The loader
        must synthesize the opening from the params and IGNORE that row --
        exactly one opening fact, value-identical to the synthesis -- so
        pre-retirement loans and post-retirement loans walk the same facts.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            # The fixture seeded a stored origination row (the legacy write).
            from app.models.loan_anchor_event import (  # pylint: disable=import-outside-toplevel
                LoanAnchorEvent,
            )
            assert (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=loan.id)
                .count()
            ) == 1

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assert len(facts) == 1
            assert facts[0].is_opening is True

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

    def test_tracking_start_synthesizes_the_opening(self, app, db, seed_user):
        """A ``tracking_start`` event becomes the opening, replacing origination.

        For a mid-life-imported loan the opening is synthesized from the
        ``tracking_start`` event (its date / balance) instead of the immutable
        origination, and carries ``is_tracking_start=True`` for the display
        label.  A $180,000 tracking-start as of 2025-06-01 on a loan that
        originated at $250,000 on 2025-01-01 yields exactly one opening fact
        at 180000 / 2025-06-01, NOT the origination principal.
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
            assert len(facts) == 1
            (opening,) = [fact for fact in facts if fact.is_opening]
            assert opening.anchor_date == date(2025, 6, 1)
            assert opening.anchor_balance == Decimal("180000.00")
            assert opening.is_tracking_start is True
            # The origination principal is NOT the opening balance.
            assert opening.anchor_balance != _PRINCIPAL

    def test_latest_tracking_start_correction_wins(self, app, db, seed_user):
        """Two tracking-start events (a correction): the latest by created_at wins.

        The table is append-only, so a correction is another ``tracking_start``
        row.  The opening synthesizes from the MOST RECENTLY created one -- a
        second, later-committed $180,000/2025-05-01 event supersedes the earlier
        $185,000/2025-06-01 one.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_tracking_start_event(
                _params(loan), Decimal("185000.00"), date(2025, 6, 1),
            )
            db.session.commit()
            # A later-committed correction (distinct balance + date, so the
            # same-day unique index permits it); its created_at is strictly
            # later, so it is the authoritative opening.
            insert_tracking_start_event(
                _params(loan), Decimal("180000.00"), date(2025, 5, 1),
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            (opening,) = [fact for fact in facts if fact.is_opening]
            assert opening.anchor_balance == Decimal("180000.00")
            assert opening.anchor_date == date(2025, 5, 1)
            assert opening.is_tracking_start is True

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
