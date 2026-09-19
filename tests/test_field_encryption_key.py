"""
Shekel Budget App -- Tests for the field-encryption key at start-up

``TOTP_ENCRYPTION_KEY`` became ``FIELD_ENCRYPTION_KEY`` (and its rotation
twin ``FIELD_ENCRYPTION_KEY_OLD``) in ``bank_import:X-f6b-2`` (ledger row
BI-503): the key encrypts every ciphertext column the app stores, and
``TOTP`` named one of them.  Three things this module pins:

  1. ``app.utils.field_encryption.refuse_retired_field_key_names`` is
     what an environment still spelling an old name meets.
  2. Ruling **R-BI23**: production REQUIRES the key -- ``ProdConfig``
     refuses a missing key, the docker-secret placeholder, and a value
     Fernet cannot load (retired keys included).
  3. ``create_app`` is the door for both: it INSTANTIATES the
     configuration class, which is what runs the refusals.  Until this
     step the factory passed the class to ``from_object``, which reads
     attributes and instantiates nothing, and every production refusal
     was dead at runtime.
"""

import pytest
from cryptography.fernet import Fernet

from app import create_app
from app.config import BaseConfig, ProdConfig
from app.utils.field_encryption import (
    RETIRED_FIELD_KEY_NAMES,
    refuse_retired_field_key_names,
)

# A SECRET_KEY that passes production's length and placeholder checks
# and could not be mistaken for a secret by anyone, gitleaks included.
_VALID_SECRET_KEY = "a" * 64


@pytest.fixture
def a_valid_production_config(monkeypatch):
    """Patch ``ProdConfig`` so that ONLY the field key can refuse.

    The class attributes are read at import time, so the cases below
    patch the attributes rather than the environment.  Every other
    production refusal (SECRET_KEY, DATABASE_URL, the limiter) is
    satisfied here; the field key is left to each case.
    """
    monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
    monkeypatch.setattr(
        ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel",
    )
    monkeypatch.setattr(
        ProdConfig, "RATELIMIT_STORAGE_URI", "redis://redis:6379/0",
    )
    monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY_OLD", None)


class TestTheRetiredKeyNamesRefuseStartup:
    """``TOTP_ENCRYPTION_KEY`` / ``TOTP_ENCRYPTION_KEY_OLD`` in the
    environment refuse ``create_app`` (``bank_import:X-f6b-2``, BI-503).

    Nothing reads the old names, so a value found under one is a
    configuration the operator did not carry across the rename; the app
    refuses to start rather than run with MFA unavailable and a "not
    set" warning over a key the operator believes is set.  The autouse
    ``set_field_encryption_key`` fixture sets the NEW name for every
    case here, so each refusal below fires beside a correctly named key
    -- presence of the old name is the fault, not absence of the new.
    """

    def test_the_two_retired_names_are_the_old_primary_and_its_twin(self):
        """The census the refusal reads is exactly the two old spellings."""
        assert RETIRED_FIELD_KEY_NAMES == (
            "TOTP_ENCRYPTION_KEY", "TOTP_ENCRYPTION_KEY_OLD",
        )

    def test_the_old_primary_name_is_refused_beside_the_new_one(
        self, monkeypatch,
    ):
        """A stale ``.env`` line under the old name refuses, new name or not.

        The message names the variable found, the new name, and the
        runbook section that carries the rename out.
        """
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "stale-value")
        with pytest.raises(RuntimeError) as exc:
            refuse_retired_field_key_names()
        message = str(exc.value)
        assert message.startswith("TOTP_ENCRYPTION_KEY is set")
        assert "FIELD_ENCRYPTION_KEY" in message
        assert "field_encryption_key" in message
        assert (
            '"Renaming TOTP_ENCRYPTION_KEY to FIELD_ENCRYPTION_KEY"'
            in message
        )

    def test_the_old_rotation_twin_alone_is_refused(self, monkeypatch):
        """A retired key parked under the OLD twin is the data-loss shape.

        With the primary renamed and the twin not, every ciphertext
        still written under the retired key would be unreadable while
        the app reports nothing wrong -- so the twin refuses on its
        own.
        """
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", "retired-value")
        with pytest.raises(
            RuntimeError, match=r"^TOTP_ENCRYPTION_KEY_OLD is set",
        ):
            refuse_retired_field_key_names()

    def test_both_old_names_are_named_in_one_refusal(self, monkeypatch):
        """Two stale lines are reported together, not one per restart."""
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "stale-value")
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", "retired-value")
        with pytest.raises(
            RuntimeError,
            match=r"^TOTP_ENCRYPTION_KEY, TOTP_ENCRYPTION_KEY_OLD is set",
        ):
            refuse_retired_field_key_names()

    def test_an_empty_old_name_is_not_a_configuration(self, monkeypatch):
        """An exported-but-empty old name holds no key and does not refuse.

        ``.env.example`` has shipped ``TOTP_ENCRYPTION_KEY_OLD=`` since
        C-04 (2026-05-03); an operator who renamed the primary line and
        left that empty one behind has carried the rename across.
        Empty is read as unset, the way ``fernet_list_from`` reads an
        empty primary.
        """
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "")
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", "")
        refuse_retired_field_key_names()

    def test_no_old_name_passes(self, monkeypatch):
        """The positive case: neither old name present, no refusal."""
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY_OLD", raising=False)
        refuse_retired_field_key_names()

    def test_create_app_is_the_door_in_every_environment(self, monkeypatch):
        """``create_app("testing")`` refuses before it binds an extension.

        The unit cases above grade the predicate; this one grades that
        the factory reaches it -- through ``BaseConfig.__init__``, on
        the TESTING config, so the refusal is every environment's and
        not ``ProdConfig``'s alone.  ``init_ref_cache=False`` is the
        migration host's shape, the first ``create_app`` the entrypoint
        runs -- the refusal must fire there, before ``flask db
        upgrade``, so ``shekel-deploy`` rolls the release back with the
        schema untouched.
        """
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "stale-value")
        with pytest.raises(RuntimeError, match=r"^TOTP_ENCRYPTION_KEY is set"):
            create_app("testing", init_ref_cache=False)


class TestProductionRequiresTheFieldKey:
    """Ruling **R-BI23** (developer, 2026-09-19): production refuses to
    start without a loadable ``FIELD_ENCRYPTION_KEY``.

    Under the warning-only posture this replaces, a container whose
    host ``.env`` still spelt the old name received an EMPTY new key
    (compose interpolates only the names its file states), the app
    started, ``/health`` passed, and every MFA-enrolled user met "MFA
    verification failed" at login.  The three refusals mirror
    ``SECRET_KEY``'s: missing, the docker-secret placeholder, a value
    the cipher could not build from.  ``ProdConfig.__init__`` is the
    home; ``create_app`` instantiating the class is what runs it.
    """

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_a_missing_key_is_refused(self, monkeypatch):
        """``None`` (the env var absent at import) refuses, naming the key."""
        monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY", None)
        with pytest.raises(
            ValueError, match=r"^FIELD_ENCRYPTION_KEY is required in production",
        ):
            ProdConfig()

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_an_empty_key_is_refused(self, monkeypatch):
        """``""`` (the env var present and empty) refuses the same way.

        This is the shape a stale host ``.env`` produces inside the
        container: ``FIELD_ENCRYPTION_KEY: ${FIELD_ENCRYPTION_KEY:-}``
        interpolates to the empty string.
        """
        monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY", "")
        with pytest.raises(
            ValueError, match=r"^FIELD_ENCRYPTION_KEY is required in production",
        ):
            ProdConfig()

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_the_docker_secret_placeholder_is_refused_with_its_own_hint(
        self, monkeypatch,
    ):
        """``replaced_by_docker_secret`` means the secret file did not load.

        A Fernet check alone would refuse it too, as a malformed key;
        the dedicated arm tells the operator WHICH file is missing.
        """
        monkeypatch.setattr(
            BaseConfig, "FIELD_ENCRYPTION_KEY", "replaced_by_docker_secret",
        )
        with pytest.raises(ValueError, match="known placeholder") as exc:
            ProdConfig()
        assert "/run/secrets/field_encryption_key" in str(exc.value)

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_a_value_fernet_cannot_load_is_refused(self, monkeypatch):
        """A non-Fernet primary refuses at start, not at the first decrypt."""
        monkeypatch.setattr(
            BaseConfig, "FIELD_ENCRYPTION_KEY", "not-a-fernet-key",
        )
        with pytest.raises(ValueError, match="Fernet cannot load"):
            ProdConfig()

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_a_retired_key_fernet_cannot_load_is_refused_too(
        self, monkeypatch,
    ):
        """A bad ``FIELD_ENCRYPTION_KEY_OLD`` entry refuses at start.

        ``MultiFernet`` would raise from it on the first decrypt
        otherwise -- every MFA login a 500 while the process reports
        healthy.
        """
        monkeypatch.setattr(
            BaseConfig, "FIELD_ENCRYPTION_KEY",
            Fernet.generate_key().decode(),
        )
        monkeypatch.setattr(
            BaseConfig, "FIELD_ENCRYPTION_KEY_OLD",
            Fernet.generate_key().decode() + ",not-a-fernet-key",
        )
        with pytest.raises(ValueError, match="Fernet cannot load"):
            ProdConfig()

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_a_loadable_key_with_retired_keys_is_accepted(self, monkeypatch):
        """The positive case: a real key and two retired keys construct.

        Without it a validator that refused everything would pass the
        cases above.
        """
        primary = Fernet.generate_key().decode()
        monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY", primary)
        monkeypatch.setattr(
            BaseConfig, "FIELD_ENCRYPTION_KEY_OLD",
            ",".join(Fernet.generate_key().decode() for _ in range(2)),
        )
        config = ProdConfig()
        assert config.FIELD_ENCRYPTION_KEY == primary

    def test_the_field_key_is_validated_after_the_limiter(self, monkeypatch):
        """The existing refusals keep their order; the field key is last.

        An operator misconfiguring several things sees them in the
        order the class already documented (SECRET_KEY, DATABASE_URL,
        the limiter), and only then the field key.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel",
        )
        monkeypatch.setattr(ProdConfig, "RATELIMIT_STORAGE_URI", "memory://")
        monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY", None)
        with pytest.raises(ValueError, match="RATELIMIT_STORAGE_URI"):
            ProdConfig()


class TestCreateAppInstantiatesTheConfiguration:
    """``create_app`` runs the configuration class's ``__init__``.

    Flask's ``from_object`` reads a class's uppercase attributes and
    instantiates nothing.  Measured on the tree before this step:
    ``create_app("production")`` returned an app under
    ``SECRET_KEY=short`` and ``RATELIMIT_STORAGE_URI=memory://`` -- every
    refusal ``ProdConfig.__init__`` states was dead at runtime, and only
    the entrypoint's shell twin of the SECRET_KEY check stood, in the
    container alone.  These cases open the door, not the predicate.
    """

    def test_a_short_secret_key_refuses_a_production_app(self, monkeypatch):
        """The oldest production refusal (F-016) fires through the factory."""
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "short")
        with pytest.raises(ValueError, match="at least 32 characters"):
            create_app("production", init_ref_cache=False)

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_a_missing_field_key_refuses_a_production_app(self, monkeypatch):
        """R-BI23's refusal fires through the factory, before extensions.

        ``init_ref_cache=False`` is the migration host's shape: the
        refusal lands at entrypoint step 3, before any migration runs.
        """
        monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY", None)
        with pytest.raises(
            ValueError, match=r"^FIELD_ENCRYPTION_KEY is required in production",
        ):
            create_app("production", init_ref_cache=False)
