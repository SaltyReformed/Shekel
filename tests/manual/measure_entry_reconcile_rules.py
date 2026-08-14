"""Score the candidate entry-reconciliation rules against the live data.

Read-only measurement for ``docs/audits/balance_architecture/archive/anchor_settle_partition.md``
step S1-c: it renders the grid's projected end balance for every pay period under
each candidate answer to *is this purchase already inside the balance the user
asserted?*, then rolls back.  Nothing is committed.

The rules scored:

* ``shipped``  -- the stored ``is_cleared`` flag, written by the bulk UPDATE at
  anchor true-up (``entry_service.clear_entries_for_anchor_true_up``).
* ``derived``  -- ruling R-DH (d) as ruled: reconciled iff
  ``entry_date <= max(observed_on)`` for the entry's account.
* ``order``    -- Section 10.3's option 2: the same, except an entry sharing the
  assertion's civil day reconciles only when it was RECORDED no later than the
  assertion.
* ``strict``   -- Section 10.3's option 3: ``entry_date < max(observed_on)``.
* ``none``     -- nothing reconciles (the floor: every envelope holds its whole
  budget back).

Run it against a clone, never against production::

    docker exec shekel-dev-app python tests/manual/measure_entry_reconcile_rules.py
"""

from __future__ import annotations

import sys

from app import create_app
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import pay_period_service
from app.services.balance_at import BalanceContext, grid_balance_view
from app.utils.dates import display_today, to_display_date

_RULES = ("shipped", "derived", "order", "strict", "none")


def _latest_observed(account_id):
    """Return the account's latest asserted civil day, or None."""
    return (
        db.session.query(db.func.max(AccountAnchorHistory.observed_on))
        .filter(AccountAnchorHistory.account_id == account_id)
        .scalar()
    )


def _last_assertion_instant_on(account_id, day):
    """Return the recording instant of *day*'s LAST assertion, or None."""
    return (
        db.session.query(db.func.max(AccountAnchorHistory.created_at))
        .filter(
            AccountAnchorHistory.account_id == account_id,
            AccountAnchorHistory.observed_on == day,
        )
        .scalar()
    )


def _entries_with_accounts():
    """Return ``[(entry, account_id), ...]`` for every entry in the database."""
    rows = (
        db.session.query(TransactionEntry, Transaction.account_id)
        .join(Transaction, Transaction.id == TransactionEntry.transaction_id)
        .all()
    )
    return [(entry, account_id) for entry, account_id in rows]


def _reconciled(rule, entry, account_id, observed, assertion_instant):
    """Return whether *rule* calls *entry* reconciled."""
    if rule == "shipped":
        # The stored flag, left exactly as the bulk UPDATE wrote it.
        return entry.is_cleared
    if rule == "none":
        return False
    if observed is None:
        return False
    if rule == "strict":
        return entry.entry_date < observed
    if entry.entry_date < observed:
        return True
    if entry.entry_date > observed:
        return False
    # Same civil day.
    if rule == "derived":
        return True
    # ``order``: the entry must have been recorded no later than the assertion.
    return (
        assertion_instant is not None
        and entry.created_at <= assertion_instant
    )


def _apply(rule, entries, observed_by_account, instant_by_account):
    """Overwrite every entry's ``is_cleared`` in the session per *rule*."""
    changed = 0
    for entry, account_id in entries:
        target = _reconciled(
            rule, entry, account_id,
            observed_by_account.get(account_id),
            instant_by_account.get(account_id),
        )
        if entry.is_cleared != target:
            entry.is_cleared = target
            changed += 1
    db.session.flush()
    return changed


def _columns(account, user_id):
    """Return ``{period_id: balance}`` from the grid's own producer."""
    view = grid_balance_view(account, BalanceContext.build(user_id))
    return {pid: col.balance for pid, col in view.columns.items()}


def main():
    """Score every rule and print the per-period balances side by side."""
    app = create_app()
    with app.app_context():
        today = display_today()
        account = (
            db.session.query(Account).filter_by(name="Checking").one()
        )
        user_id = account.user_id
        periods = pay_period_service.get_all_periods(user_id)
        current = pay_period_service.get_current_period(user_id, as_of=today)

        entries = _entries_with_accounts()
        account_ids = {account_id for _entry, account_id in entries}
        observed_by_account = {
            account_id: _latest_observed(account_id)
            for account_id in account_ids
        }
        instant_by_account = {
            account_id: _last_assertion_instant_on(
                account_id, observed_by_account[account_id],
            )
            for account_id in account_ids
            if observed_by_account[account_id] is not None
        }

        print(f"today (display tz)      : {today}")
        print(f"current period          : {current.start_date} .. "
              f"{current.end_date}")
        print(f"Checking latest observed: "
              f"{observed_by_account.get(account.id)}")
        instant = instant_by_account.get(account.id)
        print(f"  its last assertion at : {instant} "
              f"({to_display_date(instant) if instant else '--'})")
        print(f"entries in database     : {len(entries)}")
        print()

        results = {}
        for rule in _RULES:
            changed = _apply(
                rule, entries, observed_by_account, instant_by_account,
            )
            results[rule] = (_columns(account, user_id), changed)
            db.session.rollback()
            # The rollback discards the flag overwrite; re-load the rows so
            # the next rule starts from the stored state again.
            entries = _entries_with_accounts()

        header = f"{'period':<26}" + "".join(f"{rule:>13}" for rule in _RULES)
        print(header)
        print("-" * len(header))
        for period in periods:
            if period.end_date < current.start_date:
                continue
            label = f"{period.start_date}..{period.end_date}"
            marker = " <= current" if period.id == current.id else ""
            row = f"{label:<26}"
            for rule in _RULES:
                row += f"{results[rule][0][period.id]:>13}"
            print(row + marker)
        print()
        for rule in _RULES:
            print(f"{rule:<10} flags rewritten vs stored: "
                  f"{results[rule][1]:>3}   current period: "
                  f"{results[rule][0][current.id]}")
        # Nothing was committed; prove it.
        stored_true = (
            db.session.query(db.func.count(TransactionEntry.id))
            .filter(TransactionEntry.is_cleared.is_(True))
            .scalar()
        )
        print(f"\nstored is_cleared=TRUE rows after the run: {stored_true} "
              f"(unchanged means nothing committed)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
