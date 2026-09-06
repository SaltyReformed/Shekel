"""the end year moves onto the raise and the global horizon is deleted

The cutover of plan step **salary:S3-c**, ruling **R-SAL11** (developer,
2026-09-05): the horizon a recurring raise decays over is a fact ON THE
RAISE.  Plan step **salary:S3-b** added ``salary.salary_raises
.terminal_year`` and deliberately left it empty; this migration fills it
from the setting it replaces and then deletes that setting --
``auth.user_settings.merit_raise_horizon_years`` and its CHECK
``ck_user_settings_valid_merit_horizon``.

**THE BACKFILL IS A DEVELOPER RULING OF 2026-09-05**, taken against the
alternative of shipping the deletion alone.  An empty column means
"believed indefinitely", so deleting the setting without carrying its
value forward does not preserve the owner's belief -- it silently replaces
it with one he never stated.  Measured on his own data that day
(``$91,675.00`` base, recurring 3% COLA from 2026-07, recurring 2.5% merit
from 2027-01, horizon 5, State Pension 1.85%/yr high-4 hired 2016-05-31
retiring 2046-06-01), through the production producers
``salary_raises.apply_raises`` and ``pension_calculator
.calculate_benefit``:

* **Backfilled**, the projection does not move at all: the 2046 salary
  stays ``$192,953.19`` and the pension stays ``$8,541.71``/mo.
* **Not backfilled**, the merit raise becomes believed forever: the 2046
  salary reads ``$279,453.75`` (``+$86,500.56``) and the pension
  ``$11,936.54``/mo (``+$3,394.83``), plus the ``$28,707.44`` of employer
  contributions over 2026-2046 measured at plan step salary:S3-b.

A cutover that moves a pension by ``$3,394.83``/mo is not a cutover.

**THE VALUE IT WRITES IS READ BY MORE THAN THE PAGE IT CAME FROM**, and
both adversarial reviews of this step raised it against an earlier draft
that argued only about ``/retirement``.  ``merit_raise_horizon_years`` was a
``/retirement``-only knob; ``salary.salary_raises.terminal_year`` is read by
``salary_raises._applications``, which the PAYCHECK ENGINE calls
(``paycheck_calculator`` at the current period and over the projected pay
calendar).  Every value was ``NULL`` there before this migration, so the
engine clamped nothing; after it, a raise stops on the engine's side too.
That is the ruling's intent -- one end year, every engine -- and it is a
BEHAVIOUR CHANGE rather than a preservation wherever the cutoff falls inside
the owner's saved calendar.  **The deleted CHECK admitted ``0..50``**, so an
owner storing 0 or 1 gets a cutoff inside the ~2-year window and their
projected gross, net, generated salary transactions, grid and forecast all
move.  The developer stores 5, his calendar reaches 2028-08-10 and his
cutoff is 2031, so **no payday of his reaches it and the engine-side move is
``$0.00``** -- measured, not assumed.

**WHAT IT WRITES, and why each clause is there.**  The cutoff is
``the migration's year + the owner's stored horizon``, which is exactly
what ``pension_calculator._terminate_after_horizon`` computes in memory
today -- so the figures ``/retirement`` shows are unchanged AT THE MOMENT
THIS RUNS, whenever that is.  **They do not stay unchanged**, and that is
the ruling rather than a defect: the deleted rule recomputed the cutoff from
the render year, so it slid forward every January, while a stored year does
not move.  On the developer's data a 2031 render answered
``$9,664.17``/mo under the old rule against the frozen ``$8,541.71``, and
the gap widens each year until the tail arrives.  That evaporating tail
discount is measurement 1 of **R-SAL11** and the reason the column exists.  The year comes from ``date.today()`` in
Python rather than the database's ``CURRENT_DATE`` because the value being
preserved is the one the APP answers, and the app reads its own process
clock (``BalanceContext.build``'s ``as_of`` default); a database server on
a different timezone would otherwise shift the cutoff by a year for the
hours around a New Year.

* ``is_recurring`` only, **and this clause CHANGES BEHAVIOUR** --
  a fact two adversarial reviews of this step had to point out, because an
  earlier draft of this bullet called an end year on a one-time raise
  "provably inert" and stopped there.  That is true of the STORED column
  (``ck_salary_raises_terminal_year_not_before_effective`` bounds it) and
  FALSE of the rule being replaced: ``_terminate_after_horizon`` handed
  ``cutoff_year`` to every raise except a recurring COLA, one-time raises
  included, and ``_applications`` gates a one-time raise on
  ``eff_year <= terminal_year``.  So a one-time raise dated past the cutoff
  applied ZERO times, and HEAD's own docstring said so.  It cannot be
  backfilled (``ck_salary_raises_terminal_year_only_on_a_recurring_raise``
  forbids the value), so it keeps ``NULL`` and now applies.  Measured in
  ``tests/test_services/test_pension_calculator.py``: a one-time ``$2,000``
  dated 2035, asked at 2035 under a 2031 cutoff, answered ``$134,391.64``
  (dropped) and now answers ``$136,391.64``.  Same class, same direction as
  the ``effective_year`` clause below; the developer's 2026-09-05 ruling
  behind the CHECK is about what may be STORED, not about what the deleted
  rule did.
* Non-COLA only, joined through ``ref.raise_types`` rather than tested by
  subquery so a missing seed row cannot silently classify every raise as
  non-COLA.  A recurring COLA terminates at ``None`` under the rule being
  replaced -- inflation does not stop at a planning horizon -- so it keeps
  its ``NULL``.
* ``terminal_year IS NULL`` only, which makes the statement idempotent and
  makes a re-``upgrade`` after a ``downgrade`` leave any value the owner
  has since set by hand alone.
* ``effective_year <= cutoff`` only, and this clause CHANGES BEHAVIOUR
  rather than preserving it.  A recurring merit raise dated past the
  cutoff -- a promotion recorded for 2035 under a 2031 cutoff -- applies
  ZERO times today, because ``_terminate_after_horizon`` hands it a
  terminal year before its own effective year.  That state is measurement
  3 of the ruling: the defect the column exists to make unrepresentable,
  and ``ck_salary_raises_terminal_year_not_before_effective`` would reject
  the stored form of it outright.  Such a row is therefore left ``NULL``
  -- believed as recorded -- because the setting being deleted said
  nothing coherent about a raise that starts after it ends.  There is no
  such row on the developer's data (verified 2026-09-05: his only two
  raises are effective 2026-07 and 2027-01 against a 2031 cutoff), so the
  change is structural rather than a figure that moves.
* ``COALESCE(..., 5)`` on the horizon, because a user with no
  ``auth.user_settings`` row projects at 5 today through
  ``retirement_dashboard_service._resolve_merit_horizon``'s ``None``
  branch.  This literal is the last echo of the ``_DEFAULT_MERIT_HORIZON_
  YEARS`` constant that branch reads, which this step deletes.

**THE DOWNGRADE IS VALUE-LOSSY IN ONE DIRECTION AND CANNOT BE OTHERWISE.**
It restores the column at its server default of 5 for every owner: the
mapping from one setting to many rows is many-to-one, so the stored years
cannot be inverted back into a per-owner horizon.  It deliberately does NOT
clear ``terminal_year``, which would destroy a belief the owner may have
edited by hand since -- and it has no marker telling those rows from the
ones this migration wrote, so clearing them would be a guess.

**IT IS THEREFORE STATE-LOSSY RATHER THAN FIGURE-NEUTRAL, and an earlier
draft of this paragraph claimed the stronger thing.**  Both adversarial
reviews of this step measured it.  Restoring ``_terminate_after_horizon``
overwrites every stored end year on the PENSION path only; the paycheck
engine reads ``terminal_year`` straight off the row and always did, so after
a downgrade it sees the backfilled year where before the upgrade it saw
``NULL``.  On the developer's data that moves ``$0.00``, because his cutoff
is three years past the end of his saved calendar -- but that is a different
reason than the one this paragraph used to give, and it is the owner's data
rather than a property of the code.  **A state-lossless downgrade needs a
manual ``UPDATE salary.salary_raises SET terminal_year = NULL`` alongside
it**, and whoever runs one should be told so.

Revision ID: d4e8b1c62f07
Revises: c9a4e17b53d8
Create Date: 2026-09-05
"""
from datetime import date

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'd4e8b1c62f07'
down_revision = 'c9a4e17b53d8'
branch_labels = None
depends_on = None


#: The setting being deleted, and the CHECK that bounded it.  Named here so
#: :func:`upgrade` and :func:`downgrade` cannot drift on the spelling.
_SETTINGS_TABLE = "user_settings"
_SETTINGS_SCHEMA = "auth"
_HORIZON_COLUMN = "merit_raise_horizon_years"
_HORIZON_CHECK = "ck_user_settings_valid_merit_horizon"

#: The horizon a settings-less owner projects at today, mirroring
#: ``retirement_dashboard_service._DEFAULT_MERIT_HORIZON_YEARS`` and the
#: column's own server default.  See the module docstring.
_DEFAULT_HORIZON_YEARS = 5

#: Carry each recurring non-COLA raise's end year over from the owner's
#: global horizon, leaving every raise the horizon could not coherently
#: describe untouched.  Every clause is argued in the module docstring.
_BACKFILL = sa.text(
    """
    WITH horizon AS (
        SELECT p.id AS profile_id,
               :base_year + COALESCE(s.merit_raise_horizon_years, :default_years)
                   AS cutoff_year
        FROM salary.salary_profiles AS p
        LEFT JOIN auth.user_settings AS s ON s.user_id = p.user_id
    )
    UPDATE salary.salary_raises AS r
    SET terminal_year = h.cutoff_year
    FROM horizon AS h, ref.raise_types AS rt
    WHERE r.salary_profile_id = h.profile_id
      AND rt.id = r.raise_type_id
      AND rt.name <> 'cola'
      AND r.terminal_year IS NULL
      AND r.is_recurring
      AND r.effective_year <= h.cutoff_year
    """
)


def upgrade():
    """Carry the global horizon onto the raise rows, then delete it."""
    op.execute(
        _BACKFILL.bindparams(
            base_year=date.today().year,
            default_years=_DEFAULT_HORIZON_YEARS,
        )
    )
    op.drop_constraint(
        _HORIZON_CHECK, _SETTINGS_TABLE, type_="check", schema=_SETTINGS_SCHEMA,
    )
    op.drop_column(_SETTINGS_TABLE, _HORIZON_COLUMN, schema=_SETTINGS_SCHEMA)


def downgrade():
    """Restore the global horizon column at its default for every owner."""
    op.add_column(
        _SETTINGS_TABLE,
        sa.Column(
            _HORIZON_COLUMN, sa.Integer(), nullable=False,
            server_default=str(_DEFAULT_HORIZON_YEARS),
        ),
        schema=_SETTINGS_SCHEMA,
    )
    op.create_check_constraint(
        _HORIZON_CHECK,
        _SETTINGS_TABLE,
        f"{_HORIZON_COLUMN} >= 0 AND {_HORIZON_COLUMN} <= 50",
        schema=_SETTINGS_SCHEMA,
    )
