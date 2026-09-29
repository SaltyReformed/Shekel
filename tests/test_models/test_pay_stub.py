"""
Shekel Budget App -- the transcribed pay stub's tables (plan step salary:S11-a)

The schema half of ``S11`` (ruling **R-SAL42**): ``salary.pay_stubs`` and its
three child tables, plus ``ref.withholding_kinds``.  These tests pin what the
tables make true BY STRUCTURE, so the entry door (``S11-b``) and the engine
(``S11-c``) inherit it rather than re-check it:

* the tax catalogue is exactly :class:`~app.enums.WithholdingKindEnum`;
* every CHECK refuses its out-of-domain value BY NAME, beside the control that
  an in-domain stub is stored whole and the bound of each domain is admitted
  (a CHECK that refused everything would pass every refusal case);
* every child column is required, and each child is unique per stub;
* a stub can name only its own profile's paycheck lines, by key;
* nothing is deleted: a line a stub names, a profile holding a stub and its
  owner all refuse deletion -- in either trigger order -- while a line,
  profile or owner NO stub holds still deletes;
* a stub itself is never deleted, moved to another profile or truncated, for
  any routine writer (rulings **R-SAL44** / **R-SAL46**, the trigger family),
  while its own fields and its lines stay editable, a removed line leaving its
  table;
* the four tables are audited, the model and the migration state the same
  CHECKs, autogenerate sees no drift, the migration round-trips with every
  trigger re-installed by DEFINITION, and its downgrade refuses while a stub
  exists (ruling **R-SAL47**);
* a stub line records the kind it is printed under, which may differ from its
  line's and outlives a re-kind of the line; its kind key refuses an unknown
  kind by name and is RESTRICT; and ``9b64df71cc34`` fills each existing row
  from its line and refuses its downgrade while a row differs (ruling
  **R-SAL58**, plan step ``S11-c-1``).

The figures are the developer's 2026-08-27 paycheck as reconstructed in the
S11 handoff (base ``$3,631.70``; federal / state / Social Security / Medicare
``$0.00`` / ``$84.00`` / ``$194.71`` / ``$45.53``).  They are illustrative:
this leaf stores figures and prices nothing.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy
from alembic import op
from alembic.autogenerate import compare_metadata
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError, InternalError

from app import ref_cache
from app.audit_infrastructure import AUDITED_TABLES
from app.enums import CalcMethodEnum, PaycheckLineKindEnum, WithholdingKindEnum
from app.extensions import db
from app.pay_stub_infrastructure import (
    PAY_STUB_TRIGGERS,
    remove_pay_stub_infrastructure,
)
from app.models.pay_stub import (
    PayStub,
    PayStubLineAmount,
    PayStubOneOff,
    PayStubWithholding,
)
from app.models.paycheck_line import PaycheckLine
from app.models.ref import WithholdingKind
from app.models.salary_profile import SalaryProfile
from tests._test_helpers import load_migration_module, make_salary_profile

_MIGRATION = load_migration_module("5641f7729b68_a_pay_stub_is_transcribed_line_by_line.py")

#: Its child, which gives a stub line its own kind (plan step S11-c-1).
_KIND_MIGRATION = load_migration_module(
    "9b64df71cc34_a_stub_records_the_kind_it_prints_each_line_under.py",
)

#: The four ``salary`` tables this leaf creates, each audited.
_STUB_TABLES = (
    "pay_stubs",
    "pay_stub_line_amounts",
    "pay_stub_withholdings",
    "pay_stub_one_offs",
)

#: Each model beside its table, for the model-versus-migration CHECK comparison.
_MODELS = {
    "pay_stubs": PayStub,
    "pay_stub_line_amounts": PayStubLineAmount,
    "pay_stub_withholdings": PayStubWithholding,
    "pay_stub_one_offs": PayStubOneOff,
}

_PAYDAY = date(2026, 8, 27)
_BASE_PAY = Decimal("3631.70")

#: The 08-27 stub's four taxes, as the reconstruction reads them.
_WITHHOLDINGS = {
    WithholdingKindEnum.FEDERAL_INCOME: Decimal("0.00"),
    WithholdingKindEnum.STATE_INCOME: Decimal("84.00"),
    WithholdingKindEnum.SOCIAL_SECURITY: Decimal("194.71"),
    WithholdingKindEnum.MEDICARE: Decimal("45.53"),
}


def _profile(owner, name="Primary"):
    """A flushed salary profile of *owner*'s (the ``seed_user``-shaped dict)."""
    profile = make_salary_profile(owner, db.session, name=name)
    db.session.flush()
    return profile


def _line(profile, name, amount="310.00", kind=PaycheckLineKindEnum.PRE_TAX_DEDUCTION):
    """A flushed flat paycheck line on *profile*, pre-tax unless *kind* says otherwise."""
    line = PaycheckLine(
        salary_profile=profile,
        paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(kind),
        calc_method_id=ref_cache.calc_method_id(CalcMethodEnum.FLAT),
        name=name,
        amount=Decimal(amount),
    )
    db.session.add(line)
    db.session.flush()
    return line


def _amount(line, amount):
    """An unattached line amount naming *line*, printed under *line*'s own kind.

    A stub line records the kind it is printed under (ruling R-SAL58); these
    cases print each line under the line's own.
    """
    return PayStubLineAmount(
        paycheck_line_id=line.id,
        paycheck_line_kind_id=line.paycheck_line_kind_id,
        amount=amount,
    )


def _stub(profile, payday=_PAYDAY, base_pay=_BASE_PAY):
    """An UNFLUSHED stub for *profile*, added to the session, with no lines."""
    stub = PayStub(salary_profile_id=profile.id, payday=payday, base_pay=base_pay)
    db.session.add(stub)
    return stub


def _withholding(kind, amount):
    """An unattached withholding row for *kind*."""
    return PayStubWithholding(
        withholding_kind_id=ref_cache.withholding_kind_id(kind), amount=amount,
    )


def _one_off(name="Retroactive raise", amount=Decimal("120.00")):
    """An unattached taxable-earning one-off."""
    return PayStubOneOff(
        paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
            PaycheckLineKindEnum.TAXABLE_EARNING,
        ),
        name=name,
        amount=amount,
    )


def _whole_stub(profile, lines):
    """A committed 08-27 stub: one amount per line in *lines*, four taxes, a one-off.

    Args:
        profile: The stub's salary profile.
        lines: ``{PaycheckLine: Decimal}``, the stub's figure for each line.

    Returns:
        The committed :class:`PayStub`.
    """
    stub = _stub(profile)
    for line, amount in lines.items():
        stub.line_amounts.append(_amount(line, amount))
    for kind, amount in _WITHHOLDINGS.items():
        stub.withholdings.append(_withholding(kind, amount))
    stub.one_offs.append(_one_off())
    db.session.commit()
    return stub


def _refused(excinfo):
    """The text of a refused flush, for asserting which constraint named it."""
    return str(excinfo.value)


def _scalar(sql, **params):
    """Run a single-value SQL query on this test's connection."""
    return db.session.execute(sqlalchemy.text(sql), params).scalar()


@contextmanager
def _statements_sent():
    """Record every SQL statement this test's connection sends inside the block.

    A refused downgrade's writes cannot be read back afterwards: PostgreSQL's
    DDL is transactional, so the rollback that follows the refusal would
    undo a ``DROP`` that ran before it.  What the refusal must prove is that
    no such statement was SENT, which is what this records.

    Yields:
        The list the statements are appended to, in order.
    """
    connection = db.session.connection()
    sent = []

    def record(_connection, _cursor, statement, *_rest):
        """Keep one statement's text."""
        sent.append(statement)

    sqlalchemy.event.listen(connection, "before_cursor_execute", record)
    try:
        yield sent
    finally:
        sqlalchemy.event.remove(connection, "before_cursor_execute", record)


def _writes(sent):
    """The statements in *sent* other than a ``SELECT``."""
    return [statement for statement in sent if not statement.lstrip().upper().startswith("SELECT")]


def _run(step):
    """Drive one of the migration's steps over this test's own connection."""
    connection = db.session.connection()
    ctx = MigrationContext.configure(connection=connection)
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=connection):
            step()


class TestTheWithholdingKindsAreTheEnum:
    """``ref.withholding_kinds`` is exactly :class:`WithholdingKindEnum`."""

    def test_the_catalogue_holds_exactly_the_enums_values(self, app):
        """Four rows, named as the four members are valued, no more and no fewer."""
        with app.app_context():
            names = {row.name for row in db.session.query(WithholdingKind).all()}
            assert names == {member.value for member in WithholdingKindEnum}
            assert names == {
                "federal_income", "state_income", "social_security", "medicare",
            }

    def test_each_member_resolves_to_its_own_row(self, app):
        """The cache maps each member to a distinct id whose row carries its value."""
        with app.app_context():
            ids = {
                member: ref_cache.withholding_kind_id(member)
                for member in WithholdingKindEnum
            }
            assert len(set(ids.values())) == len(WithholdingKindEnum)
            for member, kind_id in ids.items():
                assert db.session.get(WithholdingKind, kind_id).name == member.value


class TestTheDomainIsTheDatabases:
    """Each CHECK refuses its value by name; in-domain rows are admitted."""

    def test_a_whole_stub_is_stored_whole(self, app, seed_user):
        """The control: every figure round-trips, and each line takes the stub's profile.

        Without this the refusal cases below could be satisfied by tables that
        refused every row.  ``salary_profile_id`` is never set on the line
        amounts here: the stub's ``line_amounts`` relationship joins over both
        columns of ``fk_pay_stub_line_amounts_pay_stub``, so appending copies
        the stub's profile into each amount's co-located key.
        """
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health", "310.00")
            dental = _line(profile, "Dental", "40.00")
            stub_id = _whole_stub(
                profile, {health: Decimal("310.00"), dental: Decimal("40.00")},
            ).id
            db.session.expire_all()

            stored = db.session.get(PayStub, stub_id)
            assert stored.salary_profile_id == profile.id
            assert stored.payday == _PAYDAY
            assert stored.base_pay == _BASE_PAY
            assert stored.use_for_pricing is True
            assert stored.notes is None
            assert stored.version_id == 1
            assert {
                (a.paycheck_line_id, a.amount, a.salary_profile_id)
                for a in stored.line_amounts
            } == {
                (health.id, Decimal("310.00"), profile.id),
                (dental.id, Decimal("40.00"), profile.id),
            }
            assert {
                (w.withholding_kind_id, w.amount) for w in stored.withholdings
            } == {
                (ref_cache.withholding_kind_id(kind), amount)
                for kind, amount in _WITHHOLDINGS.items()
            }
            assert [(o.name, o.amount) for o in stored.one_offs] == [
                ("Retroactive raise", Decimal("120.00")),
            ]

    def test_the_bound_of_each_domain_is_admitted(self, app, seed_user):
        """A one-cent base pay and a ``$0.00`` amount on every child are stored.

        The refusal cases probe one step OUTSIDE each domain; without this, a
        CHECK written one step too tight (``base_pay > 0.01``, ``amount > 0``)
        would pass every one of them.  ``$0.00`` is a real stub figure: the
        developer's federal withholding is ``$0.00``.
        """
        with app.app_context():
            profile = _profile(seed_user)
            line = _line(profile, "Health")
            stub = _stub(profile, base_pay=Decimal("0.01"))
            stub.line_amounts.append(
                _amount(line, Decimal("0.00")),
            )
            stub.withholdings.append(
                _withholding(WithholdingKindEnum.MEDICARE, Decimal("0.00")),
            )
            stub.one_offs.append(_one_off(amount=Decimal("0.00")))
            db.session.commit()
            db.session.expire_all()

            stored = db.session.get(PayStub, stub.id)
            assert stored.base_pay == Decimal("0.01")
            assert [a.amount for a in stored.line_amounts] == [Decimal("0.00")]
            assert [w.amount for w in stored.withholdings] == [Decimal("0.00")]
            assert [o.amount for o in stored.one_offs] == [Decimal("0.00")]

    @pytest.mark.parametrize("case, check_name", [
        ("base_pay_zero", "ck_pay_stubs_positive_base_pay"),
        ("base_pay_negative", "ck_pay_stubs_positive_base_pay"),
        ("line_amount_negative", "ck_pay_stub_line_amounts_nonneg_amount"),
        ("withholding_negative", "ck_pay_stub_withholdings_nonneg_amount"),
        ("one_off_negative", "ck_pay_stub_one_offs_nonneg_amount"),
        ("one_off_name_empty", "ck_pay_stub_one_offs_name_not_blank"),
        ("one_off_name_spaces", "ck_pay_stub_one_offs_name_not_blank"),
    ])
    def test_an_out_of_domain_value_is_refused_by_name(
        self, app, seed_user, case, check_name,
    ):
        """*case* fails *check_name* on flush.

        The refusal must NAME the constraint: a bare integrity error could be
        any of the tables' other constraints, and a CHECK dropped from its table
        would leave the sibling cases green.
        """
        with app.app_context():
            profile = _profile(seed_user)
            line = _line(profile, "Health")
            base_pay = {
                "base_pay_zero": Decimal("0.00"),
                "base_pay_negative": Decimal("-0.01"),
            }.get(case, _BASE_PAY)
            stub = _stub(profile, base_pay=base_pay)
            if case == "line_amount_negative":
                stub.line_amounts.append(
                    _amount(line, Decimal("-0.01")),
                )
            elif case == "withholding_negative":
                stub.withholdings.append(
                    _withholding(WithholdingKindEnum.STATE_INCOME, Decimal("-0.01")),
                )
            elif case == "one_off_negative":
                stub.one_offs.append(_one_off(amount=Decimal("-0.01")))
            elif case == "one_off_name_empty":
                stub.one_offs.append(_one_off(name=""))
            elif case == "one_off_name_spaces":
                stub.one_offs.append(_one_off(name="   "))

            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert check_name in _refused(excinfo)

    def test_every_child_column_is_required(self, app):
        """No column of the three child tables admits NULL; on the stub, only ``notes``.

        The ruling's own words for why (round 4): "Every column is required, so
        a row that is two things at once or a duplicate can't exist."  Read from
        the live catalogue, so a nullable column added later fails here.
        """
        with app.app_context():
            nullable = db.session.execute(sqlalchemy.text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'salary' AND is_nullable = 'YES' "
                "AND table_name IN ('pay_stubs', 'pay_stub_line_amounts', "
                "'pay_stub_withholdings', 'pay_stub_one_offs')"
            )).all()
            assert [tuple(row) for row in nullable] == [("pay_stubs", "notes")]

    def test_the_switch_defaults_on_at_both_tiers(self, app, seed_user):
        """``use_for_pricing`` is on for the ORM constructor AND a raw INSERT.

        The Python-side default serves the constructor only; the
        ``server_default`` is what a writer outside the ORM gets (fork 8a':
        "on when entered").
        """
        with app.app_context():
            profile = _profile(seed_user)
            orm_stub = _stub(profile)
            db.session.flush()
            db.session.execute(sqlalchemy.text(
                "INSERT INTO salary.pay_stubs (salary_profile_id, payday, base_pay) "
                "VALUES (:profile_id, :payday, :base_pay)"
            ), {
                "profile_id": profile.id, "payday": date(2026, 9, 10),
                "base_pay": _BASE_PAY,
            })
            raw = _scalar(
                "SELECT use_for_pricing FROM salary.pay_stubs "
                "WHERE salary_profile_id = :profile_id AND payday = :payday",
                profile_id=profile.id, payday=date(2026, 9, 10),
            )
            assert orm_stub.use_for_pricing is True
            assert raw is True
            db.session.rollback()

    def test_autogenerate_sees_no_drift_on_the_tables(self, app):
        """The migrated tables and the models agree on every column, type, default and key.

        Alembic's own comparison over the test database (migrated, never
        ``create_all``'d), scoped to the five tables this leaf creates and the
        one it adds a key to, with server defaults compared.  A CHECK is outside
        its sight; the next test covers those.
        """
        scoped = {("salary", table) for table in _STUB_TABLES} | {
            ("ref", "withholding_kinds"), ("salary", "paycheck_lines"),
        }
        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_schemas": True,
                    # Filters BOTH sides, so an older table's drift stays out of
                    # this verdict (the credit_card_params test's reasoning).
                    "include_object": lambda obj, name, type_, *_: (
                        type_ != "table" or (obj.schema, name) in scoped
                    ),
                },
            )
            assert compare_metadata(ctx, db.metadata) == []

    def test_the_models_and_the_migration_state_the_same_checks(self, app):
        """Every CHECK's SQL on each model equals the migration's, by name.

        Compared as a MAPPING per table, so a CHECK added on one side and not
        the other is a failure too.
        """
        with app.app_context():
            for table, model in _MODELS.items():
                model_checks = {
                    c.name: str(c.sqltext)
                    for c in model.__table__.constraints
                    if isinstance(c, sqlalchemy.CheckConstraint)
                }
                # Pylint: ``protected-access`` -- ``_CHECKS`` is the migration's
                # own statement of its CHECKs, read here to compare against.
                assert model_checks == _MIGRATION._CHECKS[table], table  # pylint: disable=protected-access


class TestOneOfEachPerStub:
    """One stub per payday per profile, and each child is unique within its stub."""

    def test_a_second_stub_on_one_payday_is_refused(self, app, seed_user):
        """``uq_pay_stubs_profile_payday``: fork 4, "One stub per payday per job"."""
        with app.app_context():
            profile = _profile(seed_user)
            _stub(profile)
            db.session.commit()
            _stub(profile, base_pay=Decimal("3635.84"))
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "uq_pay_stubs_profile_payday" in _refused(excinfo)

    def test_another_profile_may_hold_a_stub_on_the_same_payday(self, app, seed_user):
        """The control: the key is PER PROFILE, so a second job's stub that day stores."""
        with app.app_context():
            first = _profile(seed_user)
            second = _profile(seed_user, name="Second job")
            _stub(first)
            _stub(second, base_pay=Decimal("500.00"))
            db.session.commit()
            assert _scalar(
                "SELECT COUNT(*) FROM salary.pay_stubs WHERE payday = :payday",
                payday=_PAYDAY,
            ) == 2

    def test_a_stub_names_a_line_once(self, app, seed_user):
        """``uq_pay_stub_line_amounts_stub_line``: one figure per line per stub."""
        with app.app_context():
            profile = _profile(seed_user)
            line = _line(profile, "Health")
            stub = _stub(profile)
            stub.line_amounts.append(
                _amount(line, Decimal("310.00")),
            )
            stub.line_amounts.append(
                _amount(line, Decimal("300.00")),
            )
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "uq_pay_stub_line_amounts_stub_line" in _refused(excinfo)

    def test_a_stub_prints_a_tax_once(self, app, seed_user):
        """``uq_pay_stub_withholdings_stub_kind``: one figure per tax per stub."""
        with app.app_context():
            profile = _profile(seed_user)
            stub = _stub(profile)
            stub.withholdings.append(
                _withholding(WithholdingKindEnum.STATE_INCOME, Decimal("84.00")),
            )
            stub.withholdings.append(
                _withholding(WithholdingKindEnum.STATE_INCOME, Decimal("86.00")),
            )
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "uq_pay_stub_withholdings_stub_kind" in _refused(excinfo)

    def test_a_one_off_name_is_unique_within_its_stub(self, app, seed_user):
        """``uq_pay_stub_one_offs_stub_name``: fork 8b's "stub + name unique"."""
        with app.app_context():
            profile = _profile(seed_user)
            stub = _stub(profile)
            stub.one_offs.append(_one_off())
            stub.one_offs.append(_one_off(amount=Decimal("5.00")))
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "uq_pay_stub_one_offs_stub_name" in _refused(excinfo)


class TestAStubNamesOnlyItsOwnProfilesLines:
    """The two composite keys make a cross-profile line amount unstorable."""

    def test_a_line_of_another_profile_is_unstorable(self, app, seed_user):
        """The same owner's SECOND JOB's line cannot be named on the first job's stub.

        Both profiles are the owner's, so every single-column fact is valid; it
        is ``fk_pay_stub_line_amounts_paycheck_line`` over ``(paycheck_line_id,
        salary_profile_id)`` that refuses a line whose profile is not the
        stub's.
        """
        with app.app_context():
            first = _profile(seed_user)
            second = _profile(seed_user, name="Second job")
            second_jobs_line = _line(second, "Health")
            stub = _stub(first)
            stub.line_amounts.append(
                _amount(second_jobs_line, Decimal("310.00")),
            )
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "fk_pay_stub_line_amounts_paycheck_line" in _refused(excinfo)

    def test_a_line_of_another_owner_is_unstorable(self, app, seed_user, second_user):
        """Another owner's line is refused by the same key: the IDOR, made structural."""
        with app.app_context():
            mine = _profile(seed_user)
            theirs = _profile(second_user, name="Theirs")
            their_line = _line(theirs, "Health")
            stub = _stub(mine)
            stub.line_amounts.append(
                _amount(their_line, Decimal("310.00")),
            )
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "fk_pay_stub_line_amounts_paycheck_line" in _refused(excinfo)

    def test_a_profile_key_that_disagrees_with_the_stub_is_unstorable(
        self, app, seed_user,
    ):
        """A raw row naming the OTHER profile's line under that profile is refused too.

        This row satisfies the LINE key (the line IS the second profile's and
        the row says so), so only ``fk_pay_stub_line_amounts_pay_stub`` over
        ``(pay_stub_id, salary_profile_id)`` can refuse it: the stub belongs to
        the first profile.  Written in raw SQL because the ORM relationship
        would copy the stub's profile over the value this case needs.
        """
        with app.app_context():
            first = _profile(seed_user)
            second = _profile(seed_user, name="Second job")
            second_jobs_line = _line(second, "Health")
            stub = _stub(first)
            db.session.flush()
            with pytest.raises(IntegrityError) as excinfo:
                db.session.execute(sqlalchemy.text(
                    "INSERT INTO salary.pay_stub_line_amounts "
                    "(pay_stub_id, paycheck_line_id, salary_profile_id, "
                    "paycheck_line_kind_id, amount) "
                    "VALUES (:stub_id, :line_id, :profile_id, :kind_id, 310.00)"
                ), {
                    "stub_id": stub.id, "line_id": second_jobs_line.id,
                    "profile_id": second.id,
                    "kind_id": second_jobs_line.paycheck_line_kind_id,
                })
            db.session.rollback()
            assert "fk_pay_stub_line_amounts_pay_stub" in _refused(excinfo)


class TestNothingIsDeleted:
    """A stub is a record: what it names and what holds it cannot be deleted."""

    def test_a_line_a_stub_names_cannot_be_deleted(self, app, seed_user):
        """Fork 10: the ORM delete ``delete_line`` issues is refused by the line key."""
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health")
            _whole_stub(profile, {health: Decimal("310.00")})
            db.session.delete(db.session.get(PaycheckLine, health.id))
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "fk_pay_stub_line_amounts_paycheck_line" in _refused(excinfo)
            assert db.session.get(PaycheckLine, health.id) is not None

    def test_a_line_no_stub_names_is_still_deletable(self, app, seed_user):
        """The control, and fork 10's other half.

        "A line that no stub names can still be deleted."
        """
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health")
            dental = _line(profile, "Dental", "40.00")
            _whole_stub(profile, {health: Decimal("310.00")})
            db.session.delete(db.session.get(PaycheckLine, dental.id))
            db.session.commit()
            assert db.session.get(PaycheckLine, dental.id) is None

    def test_a_profile_holding_a_stub_cannot_be_deleted(self, app, seed_user):
        """``fk_pay_stubs_salary_profile_id`` is RESTRICT: the stub outlives nothing.

        The stub here names no line, so the stub's own profile key is the only
        path that can refuse; the control below proves the same delete
        succeeds without the stub.
        """
        with app.app_context():
            profile = _profile(seed_user)
            stub = _stub(profile)
            stub.withholdings.append(
                _withholding(WithholdingKindEnum.STATE_INCOME, Decimal("84.00")),
            )
            db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                db.session.execute(sqlalchemy.text(
                    "DELETE FROM salary.salary_profiles WHERE id = :id"
                ), {"id": profile.id})
            db.session.rollback()
            assert "fk_pay_stubs_salary_profile_id" in _refused(excinfo)
            assert db.session.get(SalaryProfile, profile.id) is not None

    def test_a_profile_holding_no_stub_still_deletes(self, app, seed_user):
        """The control: the identical raw delete succeeds when no stub holds the profile."""
        with app.app_context():
            profile = _profile(seed_user)
            _line(profile, "Health")
            db.session.commit()
            profile_id = profile.id
            db.session.execute(sqlalchemy.text(
                "DELETE FROM salary.salary_profiles WHERE id = :id"
            ), {"id": profile_id})
            db.session.commit()
            assert _scalar(
                "SELECT COUNT(*) FROM salary.salary_profiles WHERE id = :id",
                id=profile_id,
            ) == 0

    def test_trigger_order_decides_nothing(self, app, seed_user):
        """A profile delete is refused as migrated, and still refused under a CASCADE stub key.

        Ruling **R-CC32**'s principle: a delete's outcome must not depend on
        which path PostgreSQL evaluates first.  As migrated, the stub key's
        RESTRICT refuses (it fires at the first level, so the refusal names
        it; either key's name is accepted because the order is not the
        claim).  Then the stub key is re-made ``CASCADE`` inside this test's
        transaction: the cascaded delete of the stub now meets the trigger
        family's DELETE arm, which fires on a cascaded delete like any other.
        That second guard is what makes the outcome independent of trigger
        order -- this leaf's second review measured the CASCADE key refused in
        both orders with the family in place, and deleting the stub and its
        lines in one of them without it.
        """
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health")
            stub_id = _whole_stub(profile, {health: Decimal("310.00")}).id

            savepoint = db.session.begin_nested()
            with pytest.raises(IntegrityError) as excinfo:
                db.session.execute(sqlalchemy.text(
                    "DELETE FROM salary.salary_profiles WHERE id = :id"
                ), {"id": profile.id})
            savepoint.rollback()
            assert (
                "fk_pay_stubs_salary_profile_id" in _refused(excinfo)
                or "fk_pay_stub_line_amounts_paycheck_line" in _refused(excinfo)
            ), _refused(excinfo)

            db.session.execute(sqlalchemy.text(
                "ALTER TABLE salary.pay_stubs "
                "DROP CONSTRAINT fk_pay_stubs_salary_profile_id"
            ))
            db.session.execute(sqlalchemy.text(
                "ALTER TABLE salary.pay_stubs "
                "ADD CONSTRAINT fk_pay_stubs_salary_profile_id "
                "FOREIGN KEY (salary_profile_id) "
                "REFERENCES salary.salary_profiles (id) ON DELETE CASCADE"
            ))
            savepoint = db.session.begin_nested()
            with pytest.raises(InternalError, match="DELETE rejected"):
                db.session.execute(sqlalchemy.text(
                    "DELETE FROM salary.salary_profiles WHERE id = :id"
                ), {"id": profile.id})
            savepoint.rollback()
            assert db.session.get(PayStub, stub_id) is not None
            assert _scalar(
                "SELECT COUNT(*) FROM salary.pay_stub_line_amounts "
                "WHERE pay_stub_id = :id",
                id=stub_id,
            ) == 1
            db.session.rollback()

    def test_an_owner_holding_a_stub_cannot_be_deleted(self, app, seed_user):
        """The profile key's RESTRICT reaches the owner through the profile's own cascade.

        No door deletes a user; this states what one would meet.  The control
        below proves the same delete succeeds when no stub holds the profile.
        """
        with app.app_context():
            profile = _profile(seed_user)
            stub = _stub(profile)
            stub.withholdings.append(
                _withholding(WithholdingKindEnum.STATE_INCOME, Decimal("84.00")),
            )
            db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                db.session.execute(sqlalchemy.text(
                    "DELETE FROM auth.users WHERE id = :id"
                ), {"id": seed_user["user"].id})
            db.session.rollback()
            assert "fk_pay_stubs_salary_profile_id" in _refused(excinfo)

    def test_an_owner_holding_no_stub_still_deletes(self, app, seed_user):
        """The control: the identical user delete succeeds with a profile and no stub."""
        with app.app_context():
            _profile(seed_user)
            db.session.commit()
            user_id = seed_user["user"].id
            db.session.execute(sqlalchemy.text(
                "DELETE FROM auth.users WHERE id = :id"
            ), {"id": user_id})
            db.session.commit()
            assert _scalar(
                "SELECT COUNT(*) FROM auth.users WHERE id = :id", id=user_id,
            ) == 0


class TestAStubIsNeverDeletedOrMoved:
    """Rulings R-SAL44 and R-SAL46: the family refuses every routine writer, not only the doors."""

    def test_a_stub_cannot_be_deleted(self, app, seed_user):
        """A raw DELETE and the ORM's delete are both refused; every line survives."""
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health")
            stub_id = _whole_stub(profile, {health: Decimal("310.00")}).id

            with pytest.raises(InternalError, match="DELETE rejected"):
                db.session.execute(sqlalchemy.text(
                    "DELETE FROM salary.pay_stubs WHERE id = :id"
                ), {"id": stub_id})
            db.session.rollback()

            db.session.delete(db.session.get(PayStub, stub_id))
            with pytest.raises(InternalError, match="DELETE rejected"):
                db.session.flush()
            db.session.rollback()

            assert db.session.get(PayStub, stub_id) is not None
            for table, rows in (
                ("pay_stub_line_amounts", 1),
                ("pay_stub_withholdings", 4),
                ("pay_stub_one_offs", 1),
            ):
                assert _scalar(
                    f"SELECT COUNT(*) FROM salary.{table} WHERE pay_stub_id = :id",
                    id=stub_id,
                ) == rows, table

    def test_a_stub_cannot_move_to_another_profile(self, app, seed_user, second_user):
        """A stub holding only taxes cannot be re-pointed at another owner's profile.

        Such a stub has no line amount, so neither composite key anchors it;
        this leaf's adversarial review moved one with a plain ``UPDATE`` before
        the trigger existed.  The move arm is what refuses it now.
        """
        with app.app_context():
            mine = _profile(seed_user)
            theirs = _profile(second_user, name="Theirs")
            stub = _stub(mine)
            stub.withholdings.append(
                _withholding(WithholdingKindEnum.STATE_INCOME, Decimal("84.00")),
            )
            db.session.commit()
            with pytest.raises(InternalError, match="moving it to"):
                db.session.execute(sqlalchemy.text(
                    "UPDATE salary.pay_stubs SET salary_profile_id = :theirs "
                    "WHERE id = :id"
                ), {"theirs": theirs.id, "id": stub.id})
            db.session.rollback()
            assert _scalar(
                "SELECT salary_profile_id FROM salary.pay_stubs WHERE id = :id",
                id=stub.id,
            ) == mine.id

    def test_a_stubs_own_fields_stay_editable(self, app, seed_user):
        """The control: payday, base pay, the switch and notes all edit, and the version bumps.

        An ``UPDATE`` that names ``salary_profile_id`` without changing it is
        not a move either, which the move arm's ``WHEN`` clause admits.
        """
        with app.app_context():
            profile = _profile(seed_user)
            stub = _stub(profile)
            db.session.commit()
            stub.payday = date(2026, 9, 10)
            stub.base_pay = Decimal("3676.70")
            stub.use_for_pricing = False
            stub.notes = "Transcribed from the 09-10 stub"
            db.session.commit()
            db.session.execute(sqlalchemy.text(
                "UPDATE salary.pay_stubs SET salary_profile_id = salary_profile_id "
                "WHERE id = :id"
            ), {"id": stub.id})
            db.session.commit()
            db.session.expire_all()

            stored = db.session.get(PayStub, stub.id)
            assert (stored.payday, stored.base_pay) == (
                date(2026, 9, 10), Decimal("3676.70"),
            )
            assert stored.use_for_pricing is False
            assert stored.notes == "Transcribed from the 09-10 stub"
            assert stored.version_id == 2

    @pytest.mark.parametrize("table", _STUB_TABLES)
    def test_a_truncate_is_refused_by_the_tables_own_arm(self, app, table):
        """``TRUNCATE`` fires no row trigger and writes no audit row, so it is refused.

        Ruling **R-SAL46** puts an arm on all four tables, and each case must
        be refused by ITS TABLE'S arm -- the message names the table -- or a
        sibling's arm answering for a missing one would pass (this leaf's
        second review measured exactly that).  A child is truncated ``ONLY``.
        ``pay_stubs`` cannot be: its children's keys stop a bare truncate
        before any trigger fires, so it is truncated ``CASCADE`` with the three
        child arms dropped inside this test's transaction, which leaves its
        own arm the only one that can refuse.
        """
        with app.app_context():
            if table == "pay_stubs":
                for child in _STUB_TABLES[1:]:
                    db.session.execute(sqlalchemy.text(
                        f"DROP TRIGGER refuse_pay_stub_truncate ON salary.{child}"
                    ))
                statement = "TRUNCATE salary.pay_stubs CASCADE"
            else:
                statement = f"TRUNCATE ONLY salary.{table}"
            with pytest.raises(
                InternalError,
                match=rf"salary\.{table} holds transcribed pay stubs; TRUNCATE rejected",
            ):
                db.session.execute(sqlalchemy.text(statement))
            db.session.rollback()

    def test_under_a_lift_a_stub_takes_its_lines_with_it(self, app, seed_user):
        """The child stub keys CASCADE: once the guard is lifted, the parts go with the stub.

        No door deletes a stub and the trigger family refuses it.  This is the
        path a future migration that truly must remove a stub takes --
        :func:`remove_pay_stub_infrastructure` first -- and it shows no line
        of that stub outlives it.
        """
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health")
            stub_id = _whole_stub(profile, {health: Decimal("310.00")}).id
            remove_pay_stub_infrastructure(
                lambda statement: db.session.execute(sqlalchemy.text(statement))
            )
            db.session.execute(sqlalchemy.text(
                "DELETE FROM salary.pay_stubs WHERE id = :id"
            ), {"id": stub_id})
            for table in _STUB_TABLES[1:]:
                assert _scalar(
                    f"SELECT COUNT(*) FROM salary.{table} WHERE pay_stub_id = :id",
                    id=stub_id,
                ) == 0, table
            db.session.rollback()


class TestAStubIsEditedLineByLine:
    """What the entry door (S11-b) relies on to edit a stub one line at a time."""

    def test_removing_a_line_from_its_stub_deletes_it(self, app, seed_user):
        """Each collection is ``delete-orphan``: a removed line leaves its table.

        Without it, removing a line from its stub would try to clear the line's
        NOT NULL stub key rather than delete the row.
        """
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health")
            stub = _whole_stub(profile, {health: Decimal("310.00")})
            stub.line_amounts.remove(stub.line_amounts[0])
            stub.withholdings.remove(stub.withholdings[0])
            stub.one_offs.remove(stub.one_offs[0])
            db.session.commit()
            for table, rows in (
                ("pay_stub_line_amounts", 0),
                ("pay_stub_withholdings", 3),
                ("pay_stub_one_offs", 0),
            ):
                assert _scalar(
                    f"SELECT COUNT(*) FROM salary.{table} WHERE pay_stub_id = :id",
                    id=stub.id,
                ) == rows, table


class TestTheRemainingKeysAndChecks:
    """The version CHECK and the two reference keys, each refused by name."""

    def test_a_non_positive_version_is_refused(self, app, seed_user):
        """``ck_pay_stubs_version_id_positive``: a raw INSERT at version 0 is refused."""
        with app.app_context():
            profile = _profile(seed_user)
            with pytest.raises(IntegrityError) as excinfo:
                db.session.execute(sqlalchemy.text(
                    "INSERT INTO salary.pay_stubs "
                    "(salary_profile_id, payday, base_pay, version_id) "
                    "VALUES (:profile_id, :payday, :base_pay, 0)"
                ), {
                    "profile_id": profile.id, "payday": _PAYDAY,
                    "base_pay": _BASE_PAY,
                })
            db.session.rollback()
            assert "ck_pay_stubs_version_id_positive" in _refused(excinfo)

    def test_a_tax_kind_a_stub_uses_cannot_be_deleted(self, app, seed_user):
        """``fk_pay_stub_withholdings_withholding_kind_id`` is RESTRICT."""
        with app.app_context():
            profile = _profile(seed_user)
            stub = _stub(profile)
            stub.withholdings.append(
                _withholding(WithholdingKindEnum.STATE_INCOME, Decimal("84.00")),
            )
            db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                db.session.execute(sqlalchemy.text(
                    "DELETE FROM ref.withholding_kinds WHERE id = :id"
                ), {"id": ref_cache.withholding_kind_id(
                    WithholdingKindEnum.STATE_INCOME,
                )})
            db.session.rollback()
            assert "fk_pay_stub_withholdings_withholding_kind_id" in _refused(excinfo)

    def test_a_line_kind_a_one_off_uses_cannot_be_deleted(self, app, seed_user):
        """``fk_pay_stub_one_offs_paycheck_line_kind_id`` is RESTRICT.

        The one-off is a TAXABLE EARNING and no paycheck line of that kind
        exists here, so the one-off's key is the only one that can refuse.
        """
        with app.app_context():
            profile = _profile(seed_user)
            stub = _stub(profile)
            stub.one_offs.append(_one_off())
            db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                db.session.execute(sqlalchemy.text(
                    "DELETE FROM ref.paycheck_line_kinds WHERE id = :id"
                ), {"id": ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.TAXABLE_EARNING,
                )})
            db.session.rollback()
            assert "fk_pay_stub_one_offs_paycheck_line_kind_id" in _refused(excinfo)


class TestAStubLineRecordsItsOwnKind:
    """Ruling R-SAL58: a stub line keeps the kind the stub prints it under.

    The line's kind is the app's plan and stays editable; the stub's is what
    the document printed.  The figures are made up.
    """

    def test_a_stub_line_may_record_a_kind_its_line_does_not_have(self, app, seed_user):
        """A pre-tax line printed under post-tax deductions is stored as printed."""
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health", "200.00")
            stub = _stub(profile)
            post_tax = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.POST_TAX_DEDUCTION)
            stub.line_amounts.append(PayStubLineAmount(
                paycheck_line_id=health.id, paycheck_line_kind_id=post_tax,
                amount=Decimal("200.00"),
            ))
            db.session.commit()
            db.session.expire_all()

            assert [
                a.paycheck_line_kind_id for a in db.session.get(PayStub, stub.id).line_amounts
            ] == [post_tax]
            assert db.session.get(PaycheckLine, health.id).paycheck_line_kind_id == (
                ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.PRE_TAX_DEDUCTION)
            )

    def test_re_kinding_a_line_a_stub_names_moves_nothing_on_the_stub(self, app, seed_user):
        """Finding SAL-567 at the table: the line's kind changes, the stub's stays as printed.

        A raw ``UPDATE`` -- a writer no door sees -- goes through, because
        nothing refuses it: R-SAL58's "A line's kind stays editable, stub or
        not".  The stub line keeps the kind it was entered under.
        """
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health", "200.00")
            stub_id = _whole_stub(profile, {health: Decimal("200.00")}).id
            pre_tax = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.PRE_TAX_DEDUCTION)
            post_tax = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.POST_TAX_DEDUCTION)

            db.session.execute(sqlalchemy.text(
                "UPDATE salary.paycheck_lines SET paycheck_line_kind_id = :kind "
                "WHERE id = :id"
            ), {"kind": post_tax, "id": health.id})
            db.session.commit()

            assert _scalar(
                "SELECT paycheck_line_kind_id FROM salary.paycheck_lines WHERE id = :id",
                id=health.id,
            ) == post_tax
            assert _scalar(
                "SELECT paycheck_line_kind_id FROM salary.pay_stub_line_amounts "
                "WHERE pay_stub_id = :id",
                id=stub_id,
            ) == pre_tax

    def test_a_kind_the_catalogue_does_not_hold_is_refused_by_name(self, app, seed_user):
        """``fk_pay_stub_line_amounts_paycheck_line_kind_id`` refuses an unknown kind."""
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health")
            unknown = _scalar("SELECT MAX(id) + 1 FROM ref.paycheck_line_kinds")
            stub = _stub(profile)
            stub.line_amounts.append(PayStubLineAmount(
                paycheck_line_id=health.id, paycheck_line_kind_id=unknown,
                amount=Decimal("310.00"),
            ))
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "fk_pay_stub_line_amounts_paycheck_line_kind_id" in _refused(excinfo)

    def test_a_kind_a_stub_line_records_cannot_be_deleted(self, app, seed_user):
        """The kind key is RESTRICT, like the one-off's.

        The stub line records an AFTER-TAX EARNING, its line is pre-tax and no
        other row here is of that kind, so the stub line's key is the only one
        that can refuse.
        """
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health")
            after_tax = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.AFTER_TAX_EARNING)
            stub = _stub(profile)
            stub.line_amounts.append(PayStubLineAmount(
                paycheck_line_id=health.id, paycheck_line_kind_id=after_tax,
                amount=Decimal("310.00"),
            ))
            db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                db.session.execute(sqlalchemy.text(
                    "DELETE FROM ref.paycheck_line_kinds WHERE id = :id"
                ), {"id": after_tax})
            db.session.rollback()
            assert "fk_pay_stub_line_amounts_paycheck_line_kind_id" in _refused(excinfo)


class TestTheTablesAreAudited:
    """The four tables are in the audited set and each write lands in the log."""

    def test_the_four_tables_are_in_the_audited_set(self):
        """``AUDITED_TABLES`` names each, so the entrypoint's trigger count includes them."""
        for table in _STUB_TABLES:
            assert ("salary", table) in AUDITED_TABLES, table

    def test_a_whole_stub_leaves_one_insert_per_row(self, app, seed_user):
        """One stub, two line amounts, four taxes, one one-off: 1 / 2 / 4 / 1 INSERT rows."""
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health")
            dental = _line(profile, "Dental", "40.00")
            _whole_stub(profile, {health: Decimal("310.00"), dental: Decimal("40.00")})
            expected = {
                "pay_stubs": 1,
                "pay_stub_line_amounts": 2,
                "pay_stub_withholdings": 4,
                "pay_stub_one_offs": 1,
            }
            for table, rows in expected.items():
                assert _scalar(
                    "SELECT COUNT(*) FROM system.audit_log "
                    "WHERE table_schema = 'salary' AND table_name = :table "
                    "AND operation = 'INSERT'",
                    table=table,
                ) == rows, table


def _trigger_definitions():
    """Every trigger this migration owns, as ``{(name, table): definition}``.

    The four audit triggers and every attachment of the pay-stub refusal
    family, read back through ``pg_get_triggerdef`` so a trigger that exists
    under the right name but fires on the wrong events is a difference too.
    """
    wanted = [(f"audit_{table}", f"salary.{table}") for table in _STUB_TABLES]
    wanted += list(PAY_STUB_TRIGGERS)
    return {
        (name, table): _scalar(
            "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgname = :name AND tgrelid = to_regclass(:table)",
            name=name, table=table,
        )
        for name, table in wanted
    }


def _schema_objects():
    """Which of this migration's objects exist on this test's connection.

    Returns:
        ``{name: bool}`` for the five tables, every trigger the migration
        owns, the refusal family's function and the ``paycheck_lines``
        superkey.
    """
    found = {}
    for schema, table in [("ref", "withholding_kinds")] + [
        ("salary", table) for table in _STUB_TABLES
    ]:
        found[f"{schema}.{table}"] = bool(_scalar(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_name = :table",
            schema=schema, table=table,
        ))
    for (name, table), definition in _trigger_definitions().items():
        found[f"{name} on {table}"] = definition is not None
    found["salary.refuse_pay_stub_loss()"] = bool(_scalar(
        "SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n "
        "ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'salary' AND p.proname = 'refuse_pay_stub_loss'"
    ))
    found["uq_paycheck_lines_id_profile"] = bool(_scalar(
        "SELECT COUNT(*) FROM pg_constraint WHERE conname = :name",
        name="uq_paycheck_lines_id_profile",
    ))
    return found


class TestTheMigrationRoundTrips:
    """``5641f7729b68``'s two steps, driven over this test's own clone."""

    def test_the_downgrade_removes_everything_and_the_upgrade_restores_it(
        self, app, seed_user,
    ):
        """Down: every object gone.  Up: all back, each trigger as the tree defines it, and usable.

        The template this test starts from re-applies the audit triggers and
        the refusal family from the in-code definitions AFTER the migration
        chain, so only the downgrade-then-upgrade here grades what the
        MIGRATION installs -- which is all production ever gets.  Each
        re-installed trigger is compared by definition, not by name: an audit
        trigger firing on INSERT alone would pass a name check and lose fork
        4's "the audit log keeps the old figures".

        The stub stored after the upgrade carries a line amount, so the
        restored superkey and both composite keys are exercised, not merely
        present.  It carries no withholding: the recreated catalogue's ids are
        assigned afresh, and this process's ref cache still holds the ones it
        read at start.

        The chain is driven IN ORDER: ``9b64df71cc34`` (the stub line's own
        kind, ruling R-SAL58) is this revision's child, so it is stepped down
        first and back up last, and the stored amount carries the kind the
        model now requires.  Three revisions follow it, and none is stepped:
        ``cddb15ffba5f`` (recurrence:R23) and ``1c569c51b449``
        (recurrence:R5-a) touch no salary object, and ``9b2c5656eed9``
        (salary:X-av-1) adds only a unique key over
        ``salary.salary_profiles``' own ``scenario_id`` and ``template_id``,
        which nothing the two stepped revisions create or drop depends on.
        """
        with app.app_context():
            assert all(_schema_objects().values()), _schema_objects()
            applied = _trigger_definitions()
            audit_shape = _scalar(
                "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
                "WHERE tgname = 'audit_paycheck_lines'"
            )

            _run(_KIND_MIGRATION.downgrade)
            _run(_MIGRATION.downgrade)
            db.session.commit()
            assert not any(_schema_objects().values()), _schema_objects()

            _run(_MIGRATION.upgrade)
            _run(_KIND_MIGRATION.upgrade)
            db.session.commit()
            assert all(_schema_objects().values()), _schema_objects()
            installed = _trigger_definitions()
            assert installed == applied
            for table in _STUB_TABLES:
                assert installed[(f"audit_{table}", f"salary.{table}")] == (
                    audit_shape
                    .replace("audit_paycheck_lines", f"audit_{table}")
                    .replace("salary.paycheck_lines", f"salary.{table}")
                ), table
            assert {
                row.name for row in db.session.query(WithholdingKind).all()
            } == {member.value for member in WithholdingKindEnum}

            profile = _profile(seed_user)
            health = _line(profile, "Health")
            stub = _stub(profile)
            stub.line_amounts.append(
                _amount(health, Decimal("310.00")),
            )
            db.session.commit()
            assert _scalar(
                "SELECT COUNT(*) FROM salary.pay_stub_line_amounts "
                "WHERE pay_stub_id = :id",
                id=stub.id,
            ) == 1

    def test_the_downgrade_refuses_while_a_stub_exists(self, app, seed_user):
        """R-SAL47: with one stub stored the downgrade raises, naming the count, sending nothing.

        The schema the downgrade returns to has nowhere to hold a stub, so a
        rollback after the owner has transcribed one would destroy it.  That
        the refusal comes before any DDL is read off the connection -- it sends
        no statement but a ``SELECT`` -- since the rollback after it would
        restore a dropped object either way; every object and the stub are
        still there afterwards.
        """
        with app.app_context():
            profile = _profile(seed_user)
            _stub(profile)
            db.session.commit()
            with _statements_sent() as sent, pytest.raises(
                RuntimeError, match=r"1 transcribed pay stub\(s\) exist",
            ):
                _run(_MIGRATION.downgrade)
            db.session.rollback()
            assert _writes(sent) == []
            assert all(_schema_objects().values()), _schema_objects()
            assert _scalar("SELECT COUNT(*) FROM salary.pay_stubs") == 1


def _kind_column():
    """The stub line's kind column and its key, as the catalogue sees them.

    Returns:
        ``(is_nullable, key_present)``: ``is_nullable`` is the column's
        ``information_schema`` answer (``"NO"`` when required), or ``None``
        when the column does not exist.
    """
    nullable = _scalar(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'salary' AND table_name = 'pay_stub_line_amounts' "
        "AND column_name = 'paycheck_line_kind_id'"
    )
    key = bool(_scalar(
        "SELECT COUNT(*) FROM pg_constraint WHERE conname = :name",
        name="fk_pay_stub_line_amounts_paycheck_line_kind_id",
    ))
    return nullable, key


class TestTheKindMigrationRoundTrips:
    """``9b64df71cc34`` (ruling R-SAL58): the stub line's own kind, filled from its line.

    Driven over this test's own clone.  While the revision is stepped down the
    model is AHEAD of the table, so every write in that window is raw SQL.  The
    figures are made up.
    """

    def test_the_upgrade_fills_each_stub_lines_kind_from_the_line_it_names(
        self, app, seed_user,
    ):
        """Down: column and key gone.  Up: both back, required, each row its line's kind.

        The row written while stepped down has no kind at all, so only the
        upgrade's fill can give it one; the two lines are of two different
        kinds, so a fill that wrote one constant would fail one of them.
        """
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health", "200.00")
            roth = _line(profile, "Roth", "100.00", kind=PaycheckLineKindEnum.POST_TAX_DEDUCTION)
            stub_id = _whole_stub(profile, {health: Decimal("200.00")}).id
            assert _kind_column() == ("NO", True)

            _run(_KIND_MIGRATION.downgrade)
            db.session.commit()
            assert _kind_column() == (None, False)
            db.session.execute(sqlalchemy.text(
                "INSERT INTO salary.pay_stub_line_amounts "
                "(pay_stub_id, paycheck_line_id, salary_profile_id, amount) "
                "VALUES (:stub_id, :line_id, :profile_id, 100.00)"
            ), {"stub_id": stub_id, "line_id": roth.id, "profile_id": profile.id})
            db.session.commit()

            _run(_KIND_MIGRATION.upgrade)
            db.session.commit()
            assert _kind_column() == ("NO", True)
            filled = dict(db.session.execute(sqlalchemy.text(
                "SELECT paycheck_line_id, paycheck_line_kind_id "
                "FROM salary.pay_stub_line_amounts WHERE pay_stub_id = :id"
            ), {"id": stub_id}).all())
            assert filled == {
                health.id: ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.PRE_TAX_DEDUCTION),
                roth.id: ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.POST_TAX_DEDUCTION),
            }

    def test_the_upgrade_refuses_to_require_a_kind_the_fill_left_empty(
        self, app, seed_user,
    ):
        """The database rules' zero-NULL check, forced to fire: a fill that writes nothing.

        No database this chain builds can reach it (the upgrade's docstring
        argues why), so the fill is replaced by a statement that writes
        nothing, leaving the row written while stepped down without a kind.
        The upgrade must then refuse, naming the row, before the column is
        required.
        """
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health", "200.00")
            stub_id = _whole_stub(profile, {health: Decimal("200.00")}).id
            _run(_KIND_MIGRATION.downgrade)
            db.session.commit()
            with patch.object(_KIND_MIGRATION, "_BACKFILL_SQL", "SELECT 1"), pytest.raises(
                RuntimeError,
                match=r"1 pay stub line\(s\) were left without a kind by the fill",
            ) as refused:
                _run(_KIND_MIGRATION.upgrade)
            db.session.rollback()
            assert f"{stub_id}, {health.id})" in str(refused.value)

    def test_the_downgrade_refuses_while_a_stub_line_records_its_own_kind(
        self, app, seed_user,
    ):
        """The one lossy case: the older schema would re-kind the row to its line's.

        It raises naming the count having SENT no statement but a ``SELECT``
        -- read off the connection, since the rollback after it would undo a
        ``DROP`` either way -- and the column, its key and the row's own kind
        are all still there afterwards.  The control is the previous test, whose
        downgrade over a stub whose kinds agree succeeds.
        """
        with app.app_context():
            profile = _profile(seed_user)
            health = _line(profile, "Health", "200.00")
            post_tax = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.POST_TAX_DEDUCTION)
            stub = _stub(profile)
            stub.line_amounts.append(PayStubLineAmount(
                paycheck_line_id=health.id, paycheck_line_kind_id=post_tax,
                amount=Decimal("200.00"),
            ))
            db.session.commit()

            with _statements_sent() as sent, pytest.raises(
                RuntimeError, match=r"1 pay stub line\(s\) record a kind their paycheck line",
            ):
                _run(_KIND_MIGRATION.downgrade)
            db.session.rollback()
            assert _writes(sent) == []
            assert _kind_column() == ("NO", True)
            assert _scalar(
                "SELECT paycheck_line_kind_id FROM salary.pay_stub_line_amounts "
                "WHERE pay_stub_id = :id",
                id=stub.id,
            ) == post_tax
