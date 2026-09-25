"""a salary is a dated pay list: what one paycheck pays, from a payday on

Revision ID: 70680a4a7405
Revises: 1c569c51b449
Create Date: 2026-09-25

Plan step **salary:X-av-3a**, rulings **R-SAL59** ("Dated pay list +
forecasts"), **R-SAL60** ("Each raise rounds") and **R-SAL82** ("Yearly pay
carries, said"), closing finding **N-237** and the app half of **N-391**::

    salary.pay_entries                   + table (audited)
    salary.salary_profiles               - annual_salary,
                                         - ck_salary_profiles_positive_salary

**The salary's stored fact becomes what ONE paycheck pays, from a dated payday
on.**  ``annual_salary`` was one undated yearly figure for all time: editing
it re-priced every paycheck not yet received, back to January, and a typo fix
looked exactly like a raise.  A pay entry is dated, so a raise received is a
new entry and a correction moves only the paychecks its entry covers.  The
yearly figure is the amount times the paychecks a year of the rhythm in force
on its payday: derived, shown, never stored.

**Each profile gets ONE entry**: its yearly salary over its owner's paychecks
a year, rounded half-up to the cent, dated on the owner's FIRST saved payday.
The engine prices a payday at the latest entry on or before it (else the
first, so every paycheck before the entry uses it) and then applies every
forecast raise landing AFTER that entry's payday -- so an entry dated before
every raise's first landing prices exactly the raises the yearly salary was
priced under.  The one difference is WHERE a raise rounds: on the yearly
figure once, before; on each per-paycheck step, after (R-SAL60).  Measured on
the 2026-09-23 production clone by local arithmetic: exactly-one-cent moves on
some projected paydays from 2028 on and none earlier; no settled record moves,
because a settled record holds its own figure.

**It REFUSES, before writing anything, the three states it cannot convert
exactly**, naming each profile.  The developer's production data holds none of
them (one profile, one pay era, its first raise landing months after its first
payday; measured on the 2026-09-23 clone).

  1. **An owner with no saved payday**, or with no pay era: there is no payday
     to date the entry on, or no rhythm to divide by.
  2. **An owner with more than one pay era.**  The paychecks a year the entry
     divides by is the rhythm in force on its payday, and which era covers the
     first saved payday is answered by the calendar's placement rule
     (``pay_calendar.cadence_on``), which reads in cash days and is not
     spelled here.  One era answers it trivially; more than one is refused
     rather than guessed at.
  3. **A raise landing on or before the first saved payday.**  An entry
     REPLACES every forecast raise due on or before its payday (R-SAL59), so
     such a raise would silently stop applying.  The owner moves the raise or
     the first payday and upgrades again.

**The downgrade REFUSES while any profile holds more than one entry**, naming
how many (ruling **R-SAL47**'s shape): the older schema has one number per
profile, and a second entry is history that number cannot hold.  It refuses a
profile holding NO entry (no door leaves one; there is nothing to restore
from), an owner with more than one pay era for the upgrade's reason, and --
the upgrade's third refusal read from the other side -- **a raise landing on
or before the profile's entry**: the entry already holds that raise, and the
older engine applies every raise to the one yearly figure, so it would apply
it a second time.  The create form's default payday (the current one) dates
an entry after this year's raise, so the state is the ordinary one: made-up,
``$2,060.00`` from 2026-09-24 over a 3% July 2026 raise restores
``$53,560.00``, which the older engine prices at ``$2,121.80`` a paycheck from
July on.  It needs no saved payday, because it dates nothing.  Otherwise it
restores ``annual_salary`` as the entry times its paychecks a year, which is
the entry's yearly figure: a round trip moves the stored yearly figure to a
multiple of the paychecks a year (``$52,000.13`` becomes ``$52,000.26`` at 26
a year, made-up figures: 52,000.13 / 26 = 2,000.005, half-up 2,000.01, times
26), and with no raise landing on or before the entry it prices every
paycheck identically, up to the per-step rounding of each raise
(**R-SAL60**) that the older engine rounds once.

**A rollback does not restore the pre-upgrade yearly figure, and so can
re-price projected paychecks by a cent** (ruling **R-SAL92**, "Accept and
declare").  The upgrade keeps only the per-paycheck amount (R-SAL59): the
remainder the division rounded off is discarded, and nothing stores the old
yearly figure a second time.  The restored figure is the entry times its
paychecks a year, higher or lower than the old one wherever it did not divide
evenly, and the older engine prices every projected paycheck from it again.
Before a raise the two agree (``$2,000.01`` a paycheck either way in the
example above); after raises they can part by a cent.  Made-up:
``$52,000.13`` at 26 a year with a 3% raise compounding yearly.  In the
fourth raised year the older engine priced ``$2,251.02`` a paycheck before
the upgrade (``$58,526.60 / 26``) and prices ``$2,251.03`` after a rollback
(the restored ``$52,000.26`` raised to ``$58,526.75``, ``/ 26``).  Settled
records never move: each holds its own figure.

Literals a stored value is derived from are spelled here, not imported (the
standing rule ``f2b7c40d918e`` states): the mean Gregorian year and the two
month-kind counts are :attr:`app.services.pay_calendar.PayCadence
.periods_per_year`'s, as of this revision.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "70680a4a7405"
down_revision = "1c569c51b449"
branch_labels = None
depends_on = None


_SCHEMA = "salary"
_TABLE = "pay_entries"

#: Days in the mean Gregorian year (``pay_calendar.DAYS_PER_YEAR``).
_DAYS_PER_YEAR = "365.2425"

#: One era's paychecks a year, as :attr:`~app.services.pay_calendar.PayCadence
#: .periods_per_year` derives it from the era's columns: a fixed-days era
#: ``round(365.2425 / days)`` (``numeric`` rounds half away from zero, the
#: ``ROUND_HALF_UP`` the property states, and no day count in 1..365 lands on
#: an exact half); a semi-monthly era (``other_day`` present) 24; a monthly era
#: 12.  ``ck_pay_eras_one_kind`` makes the three arms disjoint.
_ERA_PAYCHECKS_A_YEAR = (
    "CASE WHEN e.cadence_days IS NOT NULL "
    f"THEN round({_DAYS_PER_YEAR}::numeric / e.cadence_days) "
    "WHEN e.other_day IS NOT NULL THEN 24 ELSE 12 END"
)

#: Each profile's owner's first saved payday and the count of their eras.
_OWNER_FACTS = (
    "SELECT sp.id, sp.name, "
    "(SELECT min(pp.start_date) FROM budget.pay_periods pp "
    " WHERE pp.user_id = sp.user_id) AS first_payday, "
    "(SELECT count(*) FROM budget.pay_eras e "
    " WHERE e.user_id = sp.user_id) AS eras "
    "FROM salary.salary_profiles sp"
)

#: Every raise whose first landing (the 1st of its effective month, the day
#: ``salary_raises`` applies it from) is on or before its profile's first
#: saved payday.
_RAISES_LANDING_BY_FIRST_PAYDAY = sa.text(
    "SELECT o.id, o.name, r.id, r.effective_year, r.effective_month, "
    "o.first_payday "
    f"FROM ({_OWNER_FACTS}) o "
    "JOIN salary.salary_raises r ON r.salary_profile_id = o.id "
    "WHERE o.first_payday IS NOT NULL "
    "AND make_date(r.effective_year, r.effective_month, 1) <= o.first_payday "
    "ORDER BY o.id, r.id"
)

#: One entry per profile: its yearly salary over its one era's count, half-up
#: to the cent, on its owner's first saved payday.
_WRITE_ONE_ENTRY_PER_PROFILE = sa.text(
    f"INSERT INTO {_SCHEMA}.{_TABLE} (salary_profile_id, payday, amount) "
    "SELECT sp.id, "
    "(SELECT min(pp.start_date) FROM budget.pay_periods pp "
    " WHERE pp.user_id = sp.user_id), "
    f"round(sp.annual_salary / ({_ERA_PAYCHECKS_A_YEAR}), 2) "
    "FROM salary.salary_profiles sp "
    "JOIN budget.pay_eras e ON e.user_id = sp.user_id"
)

#: Every raise whose first landing is on or before its profile's pay entry --
#: which the entry already holds, and which the older schema's one yearly
#: figure would take again.  Read after the history refusal, so each profile
#: holds exactly one entry.
_RAISES_LANDING_BY_THE_ENTRY = sa.text(
    "SELECT sp.id, sp.name, r.id, r.effective_year, r.effective_month, "
    "pe.payday "
    "FROM salary.salary_profiles sp "
    f"JOIN {_SCHEMA}.{_TABLE} pe ON pe.salary_profile_id = sp.id "
    "JOIN salary.salary_raises r ON r.salary_profile_id = sp.id "
    "WHERE make_date(r.effective_year, r.effective_month, 1) <= pe.payday "
    "ORDER BY sp.id, r.id"
)

#: How many profiles hold more than one entry, for the downgrade's refusal.
_PROFILES_WITH_HISTORY = sa.text(
    f"SELECT count(*) FROM (SELECT salary_profile_id FROM {_SCHEMA}.{_TABLE} "
    "GROUP BY salary_profile_id HAVING count(*) > 1) h"
)

#: Every profile holding NO entry, which no door leaves behind (the create
#: form writes the first, and nothing removes the last) and which has no
#: yearly figure to restore.
_PROFILES_WITHOUT_PAY = sa.text(
    "SELECT sp.id, sp.name FROM salary.salary_profiles sp "
    f"WHERE NOT EXISTS (SELECT 1 FROM {_SCHEMA}.{_TABLE} pe "
    "WHERE pe.salary_profile_id = sp.id) ORDER BY sp.id"
)

#: Each profile's yearly figure back from its one entry.
_RESTORE_ANNUAL = sa.text(
    "UPDATE salary.salary_profiles sp "
    f"SET annual_salary = pe.amount * ({_ERA_PAYCHECKS_A_YEAR}) "
    f"FROM {_SCHEMA}.{_TABLE} pe, budget.pay_eras e "
    "WHERE pe.salary_profile_id = sp.id AND e.user_id = sp.user_id"
)


def _refuse_unconvertible_owners(
    bind, direction: str, *, dates_an_entry: bool,
) -> None:
    """Refuse a profile whose owner has not exactly one era, or (upgrade) no saved payday.

    Args:
        bind: The migration's connection.
        direction: ``"upgrade"`` or ``"downgrade"``, for the message.
        dates_an_entry: Whether this direction dates an entry on the first
            saved payday -- the upgrade does, so an owner with none is
            refused; the downgrade dates nothing and asks only the count.

    Raises:
        RuntimeError: Naming each such profile; nothing is written.
    """
    facts = bind.execute(sa.text(_OWNER_FACTS + " ORDER BY sp.id")).fetchall()
    unconvertible = [
        f"profile {profile_id} ({name}): "
        + (
            "no saved payday" if dates_an_entry and first_payday is None
            else f"{eras} pay eras"
        )
        for profile_id, name, first_payday, eras in facts
        if (dates_an_entry and first_payday is None) or eras != 1
    ]
    if unconvertible:
        raise RuntimeError(
            f"the pay-list {direction} converts a salary at its owner's one "
            "pay rhythm, dated on their first saved payday, and refuses "
            f"rather than guessing: {'; '.join(unconvertible)}."
        )


def upgrade():
    """Refuse the unconvertible states, then move each salary onto one pay entry.

    Raises:
        RuntimeError: An owner with no saved payday or not exactly one pay
            era, or a raise landing on or before its profile's first saved
            payday -- each named; nothing is written.
    """
    bind = op.get_bind()
    _refuse_unconvertible_owners(bind, "upgrade", dates_an_entry=True)
    early = bind.execute(_RAISES_LANDING_BY_FIRST_PAYDAY).fetchall()
    if early:
        listing = "; ".join(
            f"profile {profile_id} ({name}): raise {raise_id} lands "
            f"{year}-{month:02d}-01, on or before the first saved payday "
            f"{first_payday}"
            for profile_id, name, raise_id, year, month, first_payday in early
        )
        raise RuntimeError(
            "a pay entry replaces every forecast raise due on or before its "
            "payday (ruling R-SAL59), so these raises would silently stop "
            f"applying: {listing}.  Move the raise or the first payday, then "
            "upgrade again."
        )

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("salary_profile_id", sa.Integer(), nullable=False),
        sa.Column("payday", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "version_id", sa.Integer(), nullable=False, server_default="1",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("amount > 0", name="ck_pay_entries_positive_amount"),
        sa.CheckConstraint(
            "version_id > 0", name="ck_pay_entries_version_id_positive",
        ),
        sa.ForeignKeyConstraint(
            ["salary_profile_id"], ["salary.salary_profiles.id"],
            name="fk_pay_entries_salary_profile_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "salary_profile_id", "payday",
            name="uq_pay_entries_profile_payday",
        ),
        schema=_SCHEMA,
    )
    # Trigger name ``audit_<table>``: what the deploy's check
    # (``app.audit_infrastructure.require_audit_triggers``) counts by prefix.
    # Created BEFORE the entries are written, so the audit log records the
    # conversion like every later salary change.
    op.execute(
        f"CREATE TRIGGER audit_{_TABLE} "
        f"AFTER INSERT OR UPDATE OR DELETE ON {_SCHEMA}.{_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )
    bind.execute(_WRITE_ONE_ENTRY_PER_PROFILE)

    op.drop_constraint(
        "ck_salary_profiles_positive_salary", "salary_profiles",
        schema=_SCHEMA, type_="check",
    )
    op.drop_column("salary_profiles", "annual_salary", schema=_SCHEMA)


def downgrade():
    """Refuse what one yearly figure cannot hold, else restore ``annual_salary``.

    Raises:
        RuntimeError: Any profile holds more than one entry (naming how
            many) or none, an owner has not exactly one pay era, or a raise
            lands on or before its profile's entry -- each named; nothing is
            written.
    """
    bind = op.get_bind()
    with_history = bind.execute(_PROFILES_WITH_HISTORY).scalar_one()
    if with_history:
        raise RuntimeError(
            f"{with_history} salary profile(s) hold more than one pay entry, "
            "and the older schema keeps one yearly figure per profile: the "
            "pay history would be destroyed.  Remove the later entries "
            "deliberately first."
        )
    without_pay = bind.execute(_PROFILES_WITHOUT_PAY).fetchall()
    if without_pay:
        listing = "; ".join(
            f"profile {profile_id} ({name})" for profile_id, name in without_pay
        )
        raise RuntimeError(
            "these salary profiles hold no pay entry, so the older schema's "
            f"yearly figure has nothing to be restored from: {listing}."
        )
    _refuse_unconvertible_owners(bind, "downgrade", dates_an_entry=False)
    held = bind.execute(_RAISES_LANDING_BY_THE_ENTRY).fetchall()
    if held:
        listing = "; ".join(
            f"profile {profile_id} ({name}): raise {raise_id} lands "
            f"{year}-{month:02d}-01, on or before the pay entry of {payday}"
            for profile_id, name, raise_id, year, month, payday in held
        )
        raise RuntimeError(
            "a pay entry already holds every forecast raise due on or before "
            "its payday (ruling R-SAL59), and the older schema applies every "
            "raise to its one yearly figure, so these raises would be applied "
            f"a second time: {listing}.  Remove or re-date the raise, then "
            "downgrade again."
        )

    op.add_column(
        "salary_profiles",
        sa.Column("annual_salary", sa.Numeric(12, 2), nullable=True),
        schema=_SCHEMA,
    )
    bind.execute(_RESTORE_ANNUAL)
    op.alter_column(
        "salary_profiles", "annual_salary", nullable=False, schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_salary_profiles_positive_salary", "salary_profiles",
        "annual_salary > 0", schema=_SCHEMA,
    )
    op.execute(f"DROP TRIGGER IF EXISTS audit_{_TABLE} ON {_SCHEMA}.{_TABLE}")
    op.drop_table(_TABLE, schema=_SCHEMA)
