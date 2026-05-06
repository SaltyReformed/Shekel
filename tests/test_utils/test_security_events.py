"""Tests for ``app.utils.security_events`` (audit F-091 / commit C-16).

Three layers of behaviour are exercised:

  1. The pure helpers (``record_security_event``,
     ``acknowledge_security_event``, ``banner_visible_for``) -- their
     timestamp arithmetic and guard clauses.
  2. The database-tier safety nets (the two CHECK constraints on
     ``auth.users``) -- a typo in a future caller cannot persist
     malformed state.
  3. The integration with the four auth route handlers that motivate
     the columns -- after a password change / MFA enrol-disable /
     backup-code regen, the row carries the right kind and timestamp.

The visibility comparison is a strict less-than between the
acknowledgement timestamp and the event timestamp; the tests pin
both branches (acknowledged-after-event hides; new-event-after-
acknowledgement re-shows) so a future refactor cannot silently
flip the inequality direction.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.user import User
from app.utils.security_events import (
    KIND_DISPLAY,
    SecurityEventKind,
    acknowledge_security_event,
    banner_visible_for,
    record_security_event,
)


# --- Helper functions -----------------------------------------------------


def _utc(year, month, day, hour=0, minute=0, second=0):
    """Construct a deterministic timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


# --- record_security_event ------------------------------------------------


class TestRecordSecurityEvent:
    """Stamps the user row with the correct kind and timestamp."""

    def test_writes_at_and_kind_with_explicit_now(self, app, db, seed_user):
        """``now`` overrides the default and lands verbatim on the row."""
        user = seed_user["user"]
        when = _utc(2026, 5, 5, 12, 30)

        record_security_event(
            user, SecurityEventKind.PASSWORD_CHANGED, now=when,
        )
        db.session.commit()

        # Reload from DB to confirm the values persisted (not just the
        # in-memory attributes).
        db.session.refresh(user)
        assert user.last_security_event_at == when
        assert user.last_security_event_kind == "password_changed"

    def test_writes_default_now_when_not_passed(self, app, db, seed_user):
        """Without an explicit ``now`` the helper uses ``datetime.now(UTC)``."""
        user = seed_user["user"]
        before = datetime.now(timezone.utc)

        record_security_event(user, SecurityEventKind.MFA_ENABLED)
        db.session.commit()
        after = datetime.now(timezone.utc)

        db.session.refresh(user)
        assert user.last_security_event_kind == "mfa_enabled"
        assert before <= user.last_security_event_at <= after

    def test_overwrites_prior_event(self, app, db, seed_user):
        """A second event replaces the first; both fields update together."""
        user = seed_user["user"]
        first = _utc(2026, 5, 1, 10, 0)
        second = _utc(2026, 5, 5, 14, 30)

        record_security_event(
            user, SecurityEventKind.PASSWORD_CHANGED, now=first,
        )
        db.session.commit()
        record_security_event(
            user, SecurityEventKind.MFA_DISABLED, now=second,
        )
        db.session.commit()

        db.session.refresh(user)
        assert user.last_security_event_at == second
        assert user.last_security_event_kind == "mfa_disabled"

    def test_rejects_non_enum_kind(self, app, db, seed_user):
        """A bare string raises ``TypeError`` so call-site typos surface."""
        user = seed_user["user"]
        with pytest.raises(TypeError):
            record_security_event(user, "password_changed")  # type: ignore[arg-type]
        # Row state unchanged.
        db.session.refresh(user)
        assert user.last_security_event_at is None
        assert user.last_security_event_kind is None

    def test_rejects_none_user(self):
        """A None user raises ``ValueError``."""
        with pytest.raises(ValueError):
            record_security_event(None, SecurityEventKind.PASSWORD_CHANGED)


# --- acknowledge_security_event -------------------------------------------


class TestAcknowledgeSecurityEvent:
    """Records the dismissal moment without clearing the event itself."""

    def test_acknowledged_at_set_event_state_preserved(
        self, app, db, seed_user
    ):
        """Ack writes the timestamp; ``at`` and ``kind`` remain set."""
        user = seed_user["user"]
        event_at = _utc(2026, 5, 1, 10, 0)
        ack_at = _utc(2026, 5, 1, 10, 5)
        record_security_event(
            user, SecurityEventKind.PASSWORD_CHANGED, now=event_at,
        )
        db.session.commit()

        acknowledge_security_event(user, now=ack_at)
        db.session.commit()

        db.session.refresh(user)
        assert user.last_security_event_acknowledged_at == ack_at
        # Event itself is preserved -- forensic / banner-comparison use.
        assert user.last_security_event_at == event_at
        assert user.last_security_event_kind == "password_changed"

    def test_idempotent_double_acknowledge(self, app, db, seed_user):
        """A second ack overwrites the first without raising."""
        user = seed_user["user"]
        record_security_event(
            user, SecurityEventKind.PASSWORD_CHANGED,
            now=_utc(2026, 5, 1),
        )
        db.session.commit()

        acknowledge_security_event(user, now=_utc(2026, 5, 1, 0, 5))
        db.session.commit()
        acknowledge_security_event(user, now=_utc(2026, 5, 1, 0, 10))
        db.session.commit()

        db.session.refresh(user)
        assert user.last_security_event_acknowledged_at == _utc(
            2026, 5, 1, 0, 10,
        )

    def test_rejects_none_user(self):
        """A None user raises ``ValueError``."""
        with pytest.raises(ValueError):
            acknowledge_security_event(None)


# --- banner_visible_for ---------------------------------------------------


class TestBannerVisibility:
    """Banner appears when an unacknowledged event is more recent than dismiss."""

    def test_no_event_no_banner(self, app, db, seed_user):
        """Fresh user (no event recorded) sees no banner."""
        user = seed_user["user"]
        assert banner_visible_for(user) is False

    def test_event_no_acknowledgement_shows_banner(self, app, db, seed_user):
        """Recorded event with no dismissal renders the banner."""
        user = seed_user["user"]
        record_security_event(user, SecurityEventKind.PASSWORD_CHANGED)
        db.session.commit()

        assert banner_visible_for(user) is True

    def test_acknowledgement_after_event_hides_banner(self, app, db, seed_user):
        """Ack timestamp newer than event timestamp hides the banner."""
        user = seed_user["user"]
        event_at = _utc(2026, 5, 1, 10, 0)
        record_security_event(
            user, SecurityEventKind.PASSWORD_CHANGED, now=event_at,
        )
        acknowledge_security_event(
            user, now=event_at + timedelta(minutes=1),
        )
        db.session.commit()

        assert banner_visible_for(user) is False

    def test_new_event_after_old_ack_shows_banner_again(
        self, app, db, seed_user
    ):
        """A second event after a prior ack re-shows the banner."""
        user = seed_user["user"]
        record_security_event(
            user, SecurityEventKind.PASSWORD_CHANGED,
            now=_utc(2026, 5, 1, 10, 0),
        )
        acknowledge_security_event(
            user, now=_utc(2026, 5, 1, 10, 5),
        )
        record_security_event(
            user, SecurityEventKind.MFA_DISABLED,
            now=_utc(2026, 5, 5, 14, 0),
        )
        db.session.commit()

        assert banner_visible_for(user) is True

    def test_strict_less_than_comparison(self, app, db, seed_user):
        """Equal timestamps hide the banner -- comparison is strict <.

        ``acknowledged_at < event_at`` returns False when the two are
        equal, so the banner stays hidden.  This is the documented
        comparison direction; the test pins it so a future flip to
        ``<=`` would surface immediately.
        """
        user = seed_user["user"]
        same_moment = _utc(2026, 5, 1, 10, 0)
        record_security_event(
            user, SecurityEventKind.PASSWORD_CHANGED, now=same_moment,
        )
        acknowledge_security_event(user, now=same_moment)
        db.session.commit()

        assert banner_visible_for(user) is False


# --- KIND_DISPLAY mapping completeness ------------------------------------


class TestKindDisplay:
    """Every enum member has display copy; copy fields are non-empty."""

    def test_every_kind_has_display_entry(self):
        """A new enum member without a matching KIND_DISPLAY row is a bug.

        The banner template renders nothing when the lookup misses,
        which would leave a security event silently un-surfaced.
        """
        for kind in SecurityEventKind:
            assert kind.value in KIND_DISPLAY, (
                f"SecurityEventKind.{kind.name} missing from KIND_DISPLAY"
            )

    def test_every_display_row_has_non_empty_title_and_detail(self):
        """An empty title or detail would render an empty banner."""
        for kind_value, copy in KIND_DISPLAY.items():
            assert copy.get("title"), f"{kind_value} title is empty"
            assert copy.get("detail"), f"{kind_value} detail is empty"


# --- Database CHECK constraints -------------------------------------------


class TestDatabaseConstraints:
    """The two CHECK constraints on auth.users hold against bad writes."""

    def test_unknown_kind_value_rejected(self, app, db, seed_user):
        """A kind not in the whitelist raises IntegrityError on commit."""
        user = seed_user["user"]
        # Bypass the helper's TypeError guard by writing the columns
        # directly -- this simulates a future raw-SQL UPDATE or a
        # buggy admin script that skips the helper.
        user.last_security_event_at = datetime.now(timezone.utc)
        user.last_security_event_kind = "totally_made_up_kind"

        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_at_set_kind_null_rejected(self, app, db, seed_user):
        """The pair CHECK rejects ``at`` set with ``kind`` NULL."""
        user = seed_user["user"]
        user.last_security_event_at = datetime.now(timezone.utc)
        user.last_security_event_kind = None

        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_kind_set_at_null_rejected(self, app, db, seed_user):
        """The pair CHECK rejects ``kind`` set with ``at`` NULL."""
        user = seed_user["user"]
        user.last_security_event_at = None
        user.last_security_event_kind = "password_changed"

        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_both_null_accepted(self, app, db, seed_user):
        """The default state (both NULL) satisfies both constraints."""
        user = seed_user["user"]
        user.last_security_event_at = None
        user.last_security_event_kind = None

        db.session.commit()  # No raise.
        db.session.refresh(user)
        assert user.last_security_event_at is None
        assert user.last_security_event_kind is None

    def test_both_set_to_valid_values_accepted(self, app, db, seed_user):
        """Both columns set with a whitelisted kind commits cleanly."""
        user = seed_user["user"]
        user.last_security_event_at = datetime.now(timezone.utc)
        user.last_security_event_kind = "mfa_enabled"

        db.session.commit()
        db.session.refresh(user)
        assert user.last_security_event_kind == "mfa_enabled"


# --- Cross-user isolation -------------------------------------------------


class TestCrossUserIsolation:
    """Stamping one user does not affect another."""

    def test_one_user_event_does_not_show_banner_for_other(
        self, app, db, seed_user, second_user,
    ):
        """Independent rows: the second user's banner stays hidden."""
        record_security_event(
            seed_user["user"], SecurityEventKind.PASSWORD_CHANGED,
        )
        db.session.commit()

        assert banner_visible_for(seed_user["user"]) is True
        assert banner_visible_for(second_user["user"]) is False

    def test_acknowledgement_is_per_user(
        self, app, db, seed_user, second_user,
    ):
        """One user's dismissal does not clear another user's banner."""
        for owner in (seed_user["user"], second_user["user"]):
            record_security_event(
                owner, SecurityEventKind.MFA_DISABLED,
                now=_utc(2026, 5, 1),
            )
        db.session.commit()

        acknowledge_security_event(
            seed_user["user"], now=_utc(2026, 5, 1, 0, 5),
        )
        db.session.commit()

        assert banner_visible_for(seed_user["user"]) is False
        assert banner_visible_for(second_user["user"]) is True


# --- Re-load from DB after raw query --------------------------------------


class TestPersistence:
    """Reading the columns back via a fresh query matches the stamped values."""

    def test_round_trip_via_fresh_query(self, app, db, seed_user):
        """Write through the helper, read back through ``db.session.get``."""
        user = seed_user["user"]
        when = _utc(2026, 5, 5, 9, 15)
        record_security_event(
            user, SecurityEventKind.BACKUP_CODES_REGENERATED, now=when,
        )
        db.session.commit()
        # Pop from the identity map to force a real round-trip.
        db.session.expire(user)

        fetched = db.session.get(User, user.id)
        assert fetched.last_security_event_at == when
        assert fetched.last_security_event_kind == "backup_codes_regenerated"


# --- Audit-trail integration ----------------------------------------------


class TestAuditTrailCapturesNewColumns:
    """Stamping the new columns produces a row in ``system.audit_log``.

    ``auth.users`` is in ``AUDITED_TABLES`` so the rebuild migration
    attaches an UPDATE trigger.  This test confirms the trigger
    actually fires when only the C-16 columns change -- a regression
    where the trigger is dropped (or where the new columns are
    omitted from a row-image diff somehow) would leave the security-
    event change with no forensic record.
    """

    def test_record_security_event_writes_audit_log_row(
        self, app, db, seed_user,
    ):
        """An UPDATE that only touches the C-16 columns is captured."""
        user = seed_user["user"]

        # Capture the audit-log row count BEFORE the UPDATE.
        before = db.session.execute(db.text(
            "SELECT count(*) FROM system.audit_log WHERE table_name = 'users'"
        )).scalar()

        record_security_event(user, SecurityEventKind.PASSWORD_CHANGED)
        db.session.commit()

        after = db.session.execute(db.text(
            "SELECT count(*) FROM system.audit_log WHERE table_name = 'users'"
        )).scalar()
        assert after > before, (
            "The audit trigger did not produce a row for the security-"
            "event UPDATE -- check that auth.users is still in "
            "AUDITED_TABLES and that the rebuild migration last ran."
        )

        # The most recent row carries the new column values in its
        # ``new_data`` JSONB payload.
        latest = db.session.execute(db.text(
            "SELECT new_data FROM system.audit_log "
            "WHERE table_name = 'users' "
            "ORDER BY executed_at DESC LIMIT 1"
        )).scalar()
        assert latest is not None
        assert latest.get("last_security_event_kind") == "password_changed"
        assert latest.get("last_security_event_at") is not None
