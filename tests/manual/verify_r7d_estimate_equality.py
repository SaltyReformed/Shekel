"""R7d-a: prove the ESTIMATED tier's new pricing rule moves no live figure.

Dumps every forward figure a surface reads off ``balance_at``'s loan plan --
the payoff date, the projected balance on a 30-year monthly grid, and the
projected interest per year -- for every configured loan on the database this
is pointed at.  Run it on ``origin/dev`` and again on the branch and diff the
two files: an empty diff is the equality claim, measured rather than argued.

Usage (from a checkout, against a production clone)::

    DATABASE_URL=... python tests/manual/verify_r7d_estimate_equality.py OUT.txt

It reads only; it opens no transaction of its own and writes nothing.
"""
import sys
from datetime import date

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.utils.dates import add_months

_AS_OF = date(2026, 8, 25)
_GRID_MONTHS = 360
_YEARS = range(2026, 2050)


def _grid(start):
    """Return the monthly valuation grid the dump samples the balance on."""
    return [add_months(start, n) for n in range(_GRID_MONTHS + 1)]


def main(out_path):
    """Write every forward loan figure on this database to *out_path*."""
    app = create_app()
    lines = []
    with app.app_context():
        user_ids = [
            row[0] for row in db.session.query(Account.user_id).distinct().all()
        ]
        for user_id in sorted(user_ids):
            ctx = BalanceContext.build(user_id, _AS_OF)
            accounts = (
                db.session.query(Account)
                .filter_by(user_id=user_id)
                .order_by(Account.id)
                .all()
            )
            for account in accounts:
                figures = balance_at.loan_figures(account, ctx)
                if figures is None:
                    continue
                lines.append(
                    f"### user {user_id} account {account.id} {account.name}"
                )
                lines.append(f"payoff {figures.payoff_date}")
                lines.append(f"retired {figures.is_retired}")
                owed = balance_at.positions(account, ctx, _grid(_AS_OF))
                for on_date in sorted(owed):
                    lines.append(f"owed {on_date} {owed[on_date]}")
                for year in _YEARS:
                    lines.append(
                        f"interest {year} "
                        f"{balance_at.loan_interest_in_year(account, ctx, year)}"
                    )
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"{len(lines)} lines -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1])
