"""Price every saved paycheck of every active salary profile on ONE database, read-only.

Plan step **salary:R18-d**'s instrument (the OPERATOR act of 2026-09-19: the
phone allowance moved from an income template onto a paycheck line).  Run it
against two restores of the same production -- the one taken BEFORE the act and
the one taken after -- and diff the two outputs: every payday whose ``net``
differs is a payday the act moved, and by how much.  The record of that
measurement is ``docs/plans/historical/salary_r18d_as_performed_2026-09-19.md``
and ledger row **SAL-564** rests on it.

**It prices through the ONE producer** --
:func:`app.services.income_service.paycheck_pricing` -- so what it reports is
what the grid, the salary page and the cockpit show, not a re-implementation
that could agree with itself (``docs/plans/verification.md`` standard 3).  It
is READ-ONLY: nothing is assigned to an ORM attribute and the session is rolled
back at the end.

Usage (from the repository root)::

    DATABASE_URL=postgresql://shekel_user:...@127.0.0.1:5432/shekel_r18d \\
        .venv/bin/python tests/manual/measure_r18d_phone_line.py [--from 2026-07-30]

One line per saved payday from ``--from`` (default the first saved period):
``period_id``, ``payday``, ``base``, ``gross``, ``net`` and the taxable earning
lines priced on it (``name:amount``).
"""
import argparse
import logging
import os
import pathlib
import sys

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` is importable only with the repository root added
# (the shape every harness in this directory uses).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app, db
from app.models.salary_profile import SalaryProfile
from app.services import income_service
from app.services.pay_calendar import calendar_for


def main() -> int:
    """Print one priced line per saved payday; return 0."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="from_day", default="",
                        help="ISO date; paydays before it are not printed")
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL must name the database to price (a throwaway "
              "restore, never production).", file=sys.stderr)
        return 2
    # The tax calculator logs six DEBUG lines per paycheck; the table is the output.
    logging.disable(logging.INFO)
    app = create_app()
    with app.app_context():
        try:
            profiles = (
                db.session.query(SalaryProfile)
                .filter(SalaryProfile.is_active.is_(True))
                .order_by(SalaryProfile.id)
                .all()
            )
            for profile in profiles:
                calendar = calendar_for(profile.user_id)
                periods = [
                    period for period in calendar.saved()
                    if period.start_date.isoformat() >= args.from_day
                ]
                breakdowns = (
                    income_service.paycheck_pricing(calendar.user_id, lambda c=calendar: c)
                    .for_profile(profile)
                    .over(periods)
                )
                print(f"# profile {profile.id} (user {profile.user_id}), "
                      f"{len(profile.lines)} lines")
                for breakdown in breakdowns:
                    earnings = breakdown.earnings
                    lines = ",".join(
                        f"{line.name}:{line.amount}" for line in earnings.taxable
                    )
                    print(f"{breakdown.period.period_id}\t{breakdown.period.payday}\t"
                          f"base={earnings.base_biweekly}\tgross={earnings.gross_biweekly}\t"
                          f"net={earnings.net_pay}\tlines={lines}")
        finally:
            # Nothing here writes; this says so even on an exception path.
            db.session.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
