"""
Shekel Budget App -- State Machine Unit Tests

Direct tests of ``app.services.state_machine.verify_transition`` -- the
single helper that governs every legal transition for both
Transaction.status_id and Transfer.status_id.

Audit reference: F-046 / F-047 / F-161 / commit C-21 of the
2026-04-15 security remediation plan.

Coverage matrix
---------------

For every (current, new) pair where the helper either accepts or
rejects, we have one positive and one negative test.  Identity
transitions (current == new) are covered for every status because the
HTMX UI's idempotent re-submission relies on them silently
succeeding.

The helper does not consult the database -- it reads ref_cache, which
is initialised by the ``app`` fixture's create_app() flow.  All tests
therefore use ``app.app_context()`` so ``ref_cache.status_id(...)``
resolves to the live integer PKs.
"""

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.services.state_machine import verify_transition


# ── Helpers ─────────────────────────────────────────────────────────


def _ids(app):
    """Resolve all StatusEnum members to their integer PKs.

    Returned as a dict so individual tests can read ``ids["projected"]``
    etc. without re-typing ref_cache calls.
    """
    with app.app_context():
        return {
            "projected": ref_cache.status_id(StatusEnum.PROJECTED),
            "done": ref_cache.status_id(StatusEnum.DONE),
            "received": ref_cache.status_id(StatusEnum.RECEIVED),
            "credit": ref_cache.status_id(StatusEnum.CREDIT),
            "cancelled": ref_cache.status_id(StatusEnum.CANCELLED),
            "settled": ref_cache.status_id(StatusEnum.SETTLED),
        }


# ── Legal transitions from Projected ────────────────────────────────


class TestLegalTransitionsFromProjected:
    """Projected is the workflow's entry point; every active state is
    reachable from it, plus the identity edge for idempotent resubmits."""

    def test_projected_to_done(self, app):
        """Mark expense paid -- the most common transition."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["projected"], ids["done"], context="transaction")

    def test_projected_to_received(self, app):
        """Income deposited -- mirrors projected -> done for expenses."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["projected"], ids["received"], context="transaction")

    def test_projected_to_credit(self, app):
        """Mark as credit -- triggers the auto-payback workflow."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["projected"], ids["credit"], context="transaction")

    def test_projected_to_cancelled(self, app):
        """Cancel a projected item -- never paid."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["projected"], ids["cancelled"], context="transaction")

    def test_projected_to_projected_identity(self, app):
        """Idempotent re-submit of "set projected" must succeed silently."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["projected"], ids["projected"], context="transaction")


# ── Illegal transitions from Projected ──────────────────────────────


class TestIllegalTransitionsFromProjected:
    """Settled is unreachable from Projected -- a row must pass through
    Done or Received first.  The carry-forward and audit log both
    depend on this invariant."""

    def test_projected_to_settled_rejected(self, app):
        """Direct projected -> settled bypasses the Done/Received audit row."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                verify_transition(
                    ids["projected"], ids["settled"], context="transaction",
                )
            # The exception message names both endpoints so the route
            # layer can surface a clear 400 to the user.
            msg = str(excinfo.value)
            assert "transaction" in msg
            assert str(ids["projected"]) in msg
            assert str(ids["settled"]) in msg


# ── Legal transitions from Done / Received ──────────────────────────


class TestLegalTransitionsFromDoneReceived:
    """Done and Received share the same successor set: settled, projected
    (revert), and the identity edge."""

    def test_done_to_settled(self, app):
        """Archive a paid expense."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["done"], ids["settled"], context="transaction")

    def test_received_to_settled(self, app):
        """Archive received income."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["received"], ids["settled"], context="transaction")

    def test_done_to_projected_revert(self, app):
        """Revert a mistakenly-marked Paid expense back to Projected."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["done"], ids["projected"], context="transaction")

    def test_received_to_projected_revert(self, app):
        """Revert a mistakenly-marked Received income back to Projected."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["received"], ids["projected"], context="transaction")

    def test_done_to_done_identity(self, app):
        """Re-marking Paid is idempotent."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["done"], ids["done"], context="transaction")


# ── Illegal transitions from Done / Received ────────────────────────


class TestIllegalTransitionsFromDoneReceived:
    """Done and Received cannot jump sideways to Credit or Cancelled --
    the user must revert to Projected first so the audit log records
    both legs of the change."""

    def test_done_to_credit_rejected(self, app):
        """Cannot reclassify a paid expense as credit without reverting first."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(
                    ids["done"], ids["credit"], context="transaction",
                )

    def test_done_to_cancelled_rejected(self, app):
        """Cannot cancel a paid expense -- the payment already happened."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(
                    ids["done"], ids["cancelled"], context="transaction",
                )

    def test_received_to_credit_rejected(self, app):
        """Income cannot become credit -- credit is expense-only."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(
                    ids["received"], ids["credit"], context="transaction",
                )

    def test_received_to_cancelled_rejected(self, app):
        """Cannot cancel received income -- the deposit already happened."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(
                    ids["received"], ids["cancelled"], context="transaction",
                )


# ── Credit / Cancelled successors ───────────────────────────────────


class TestCreditAndCancelledSuccessors:
    """Credit and Cancelled both have a single non-identity successor:
    Projected.  This keeps the carry-forward and audit invariants intact
    -- a row reactivated from either state is observably identical to
    a freshly projected row."""

    def test_credit_to_projected(self, app):
        """unmark_credit transitions credit -> projected."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["credit"], ids["projected"], context="transaction")

    def test_cancelled_to_projected(self, app):
        """Reactivate a cancelled item back to projected."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["cancelled"], ids["projected"], context="transaction")

    def test_credit_to_done_rejected(self, app):
        """Credit -> Done would skip the auto-payback cleanup workflow."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(
                    ids["credit"], ids["done"], context="transaction",
                )

    def test_cancelled_to_done_rejected(self, app):
        """Cancelled -> Done would resurrect a row without the projected step."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(
                    ids["cancelled"], ids["done"], context="transaction",
                )


# ── Settled is terminal ─────────────────────────────────────────────


class TestSettledIsTerminal:
    """Settled is the workflow terminator; no exit is permitted.  The
    identity self-loop keeps idempotent submits safe."""

    def test_settled_to_settled_identity(self, app):
        """Re-settling a settled row succeeds silently."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(ids["settled"], ids["settled"], context="transaction")

    def test_settled_to_projected_rejected(self, app):
        """Reverting a settled row would invalidate the carry-forward audit."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(
                    ids["settled"], ids["projected"], context="transaction",
                )

    def test_settled_to_done_rejected(self, app):
        """Settled rows cannot return to any active state."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(
                    ids["settled"], ids["done"], context="transaction",
                )

    def test_settled_to_cancelled_rejected(self, app):
        """Cannot cancel an archived row -- archival is irreversible."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(
                    ids["settled"], ids["cancelled"], context="transaction",
                )


# ── Defensive: corrupt or unknown current state ─────────────────────


class TestCorruptCurrentStateRejected:
    """A row whose current status_id is not a recognised StatusEnum
    member must fail loudly -- silently accepting the transition would
    let the corrupt row drift into a worse state without an audit log
    entry pointing at the source."""

    def test_unknown_current_status_rejected(self, app):
        """An invented current_status_id is rejected with a clear message."""
        ids = _ids(app)
        # Choose a value guaranteed to fall outside the StatusEnum PK
        # range (PostgreSQL identity columns start at 1; -1 is never
        # legitimately assigned).
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                verify_transition(-1, ids["projected"], context="transaction")
            # Message must name the unknown ID so an operator can
            # locate the offending row in the audit log.
            assert "-1" in str(excinfo.value)


# ── Context label propagates to the exception message ───────────────


class TestContextLabelPropagation:
    """The context label ("transaction" / "transfer") must appear in the
    raised ValidationError so the route layer can surface a precise
    message to the user without parsing strings."""

    def test_transaction_context_label(self, app):
        """A failing transaction transition mentions "transaction"."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                verify_transition(
                    ids["settled"], ids["projected"], context="transaction",
                )
            assert "transaction" in str(excinfo.value)

    def test_transfer_context_label(self, app):
        """A failing transfer transition mentions "transfer"."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                verify_transition(
                    ids["settled"], ids["projected"], context="transfer",
                )
            assert "transfer" in str(excinfo.value)
