"""Dump every figure the LEVEL RELATION can move, for a before/after diff.

Plan step ``balance:X-bj-1`` moved the bank's statement placements into
``budget.account_anchor_history`` beside the owner's true-ups and made a
release an appended row.  Its claim about itself is ``$0.00``: the cash fold
still resets at the owner's rows alone (``balance_predicates
.owner_declared_clause``) and the bank walk still anchors on the same standing
bank level.  Every harness in ``docs/plans/verification.md`` is blind to at
least half of that -- ``verify_balance_baseline`` dumps the balance seam and
never the import package, and nothing dumped ``bank_agreement`` or
``outstanding_difference`` at all -- so this is the instrument, BUILT AND RUN
BEFORE the change (the X-g2b-0 discipline): the before-dump was taken on the
production clone ``shekel_xbj1`` at ``6c15d2a97b78`` on 2026-09-16 08:22 EDT,
9 accounts, and the after-dump differed from it in exactly one added key
(``release: null`` on the statements page's balance value).

**It can SEE the axis the defect lives on, and that was shown rather than
assumed**: with the owner-only narrowing deleted (a mutation harness that
patched ``owner_declared_clause`` to ``true``), Checking's fold reset at the
bank's 2026-07-17 level, ``asserted_total`` moved by ``$578.71`` and 42 lines
of the dump changed.

What it captures, per account:

* the bank side: ``covered_runs``, ``recorded_span``, the whole
  ``fold_bank_balances`` (its chosen anchor and every recorded day plus one
  either side) and ``recorded_opening_before`` the first recorded day;
* the cash side: ``resolve_anchor``, ``governing_anchor``,
  ``reconciled_through``, every ``cash_anchor_facts`` row,
  ``earliest_assertion_day``;
* under the read pass: ``bank_agreement`` whole (every day, every property),
  ``outstanding_difference`` whole, and the history card's rows and opening;
* the statements page's read model, ``import_history``.

Keys are positional (account id, day, import id), never a level row id, so a
re-minted id does not read as a moved figure.  It answers "did anything move",
never "is the answer right".

Usage::

    DATABASE_URL=postgresql://.../<a restore> PYTHONPATH=. \\
        python tests/manual/verify_level_relation.py before.json

Reads only.  No writes, no commit, nothing staged.
"""

import json
import sys
from datetime import date, timedelta
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.user import User
from app.services import (
    balance_at,
    bank_agreement,
    cash_ledger,
    outstanding_difference,
    statement_import,
)


def _plain(value):
    """Render a value as JSON-safe data, dataclasses and enums included."""
    if isinstance(value, Decimal):
        rendered = str(value)
    elif isinstance(value, date) or hasattr(value, "isoformat"):
        rendered = value.isoformat()
    elif hasattr(value, "value") and hasattr(type(value), "__members__"):
        rendered = value.value
    elif hasattr(value, "__dataclass_fields__"):
        rendered = {
            name: _plain(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    elif isinstance(value, dict):
        rendered = {str(_plain(k)): _plain(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        rendered = [_plain(v) for v in value]
    else:
        rendered = value
    return rendered


def _bank_side(account_id):
    """Every figure the import package answers about one account."""
    runs = statement_import.covered_runs(account_id)
    span = statement_import.recorded_span(account_id)
    folded = None
    if span.first_day is not None:
        days = []
        day = span.first_day - timedelta(days=1)
        while day <= span.last_day + timedelta(days=1):
            days.append(day)
            day += timedelta(days=1)
        folded = statement_import.fold_bank_balances(account_id, days)
    opening_before = None
    if span.first_day is not None:
        opening_before = statement_import.recorded_opening_before(
            account_id, span.first_day,
        )
    return {
        "covered_runs": _plain(runs),
        "recorded_span": _plain(span),
        "fold": None if folded is None else {
            "anchor": _plain(folded.anchor),
            "balances": _plain(folded.balances),
        },
        "recorded_opening_before_first_line": _plain(opening_before),
    }


def _cash_side(account):
    """Every figure the cash resolver and the clearing boundary answer."""
    try:
        resolved = _plain(cash_ledger.resolve_anchor(account))
    except RuntimeError as exc:
        resolved = f"RuntimeError: {exc}"
    return {
        "resolve_anchor": resolved,
        "governing_anchor": _plain(cash_ledger.governing_anchor(account.id)),
        "reconciled_through": _plain(
            cash_ledger.reconciled_through(account.id).observed_day,
        ),
        "cash_anchor_facts": [
            {
                "observed_on": f.observed_on.isoformat(),
                "anchor_balance": str(f.anchor_balance),
                "recorded_on": f.recorded_on.isoformat(),
                "is_opening": f.is_opening,
            }
            for f in cash_ledger.cash_anchor_facts(account.id)
        ],
        "earliest_assertion_day": _plain(
            cash_ledger.earliest_assertion_day(account.id)
        ),
    }


def _with_context(account, ctx):
    """The three read-pass producers, whole."""
    agreement = bank_agreement.bank_agreement(account, ctx)
    diff = outstanding_difference.outstanding_difference(account, ctx)
    history = balance_at.cash_anchor_history(account, ctx)
    out = {
        "agreement": None,
        "outstanding_difference": _plain(diff),
        "cash_anchor_history": {
            "rows": [
                {
                    k: _plain(getattr(row, k))
                    for k in row.__dataclass_fields__
                    if k not in ("anchor_id",)
                }
                for row in history.rows
            ],
            "opening": _plain(history.opening),
        } if history is not None else None,
    }
    if agreement is not None:
        out["agreement"] = {
            "span": _plain(agreement.span),
            "records_begin": _plain(agreement.records_begin),
            "anchor": _plain(getattr(agreement, "anchor", None)),
            "anchors": _plain(getattr(agreement, "anchors", None)),
            "imports": _plain(agreement.imports),
            "days": _plain(agreement.days),
            "unpriced_days": agreement.unpriced_days,
            "headline": _plain(agreement.headline),
            "standing_gap": _plain(agreement.standing_gap),
            "constant_offset": _plain(agreement.constant_offset),
            "app_ahead": str(agreement.app_ahead),
            "bank_ahead": str(agreement.bank_ahead),
            "asserted_total": str(agreement.asserted_total),
        }
    return out


def _statements_page(user_id, account_id):
    """The statements page's read model, every row."""
    records = statement_import.import_history(user_id, account_id)
    return [
        {
            "import_id": r.import_id,
            "period": [r.period_start.isoformat(), r.period_end.isoformat()],
            "line_count": r.line_count,
            "recorded_count": r.recorded_count,
            "matches_affected": r.matches_affected,
            "removes": _plain(r.removes),
            "balance": _plain(r.balance),
        }
        for r in records
    ]


def main(out_path):
    """Write the dump for every account of every user to *out_path*."""
    app = create_app()
    with app.app_context():
        out = {}
        for user in db.session.query(User).order_by(User.id):
            ctx = balance_at.BalanceContext.build(user.id)
            for account in (
                db.session.query(Account)
                .filter(Account.user_id == user.id)
                .order_by(Account.id)
            ):
                key = f"user{user.id}/account{account.id}"
                out[key] = {
                    "bank": _bank_side(account.id),
                    "cash": _cash_side(account),
                    "ctx": _with_context(account, ctx),
                    "statements_page": _statements_page(user.id, account.id),
                }
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(out, handle, indent=1, sort_keys=True)
            handle.write("\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(
            "usage: verify_level_relation.py OUT.json "
            "(DATABASE_URL selects the database)"
        )
    main(sys.argv[1])
