"""Score the app's rendered cash balance against the BANK's own daily closing.

The instrument behind ruling **balance:R-GW** (``docs/audits/balance_architecture/README.md``
Section 4), and it exists as a committed script rather than as a number in a
planning document because ``conventions.md`` rule 6 says a measurement is named
by its COMMAND: a figure copied into prose goes stale invisibly, and the four
places that ruling's evidence appears would each have gone stale separately.
**Cited with its ARC** because `bank_import:R-GW` was minted the same day on
another branch: a bare id here names two rulings, which is finding **N-367**.

**What it answers.**  Two arms, over the days an account's records and a bank
export both cover:

* **today** -- what the shipped seam renders
  (:func:`app.services.balance_at.cash_balance_at`), in which every balance
  ASSERTION resets the running total (ruling R-S);
* **the cutover** -- ``opening equity + SUM(settled sources <= day)`` with no
  reset at all, which is what plan step ``balance:X-f3c-5`` makes
  ``balance(T)``.

Both arms fold the SAME walk, so the only difference between them is the reset.
The planned tier is out of both by construction: ruling R-G clamps a
still-Projected row to ``as_of + 1``, and the reader's as-of here is the LAST
day compared.

**It reports SPLIT by whether the day carries an assertion, and that split is
the point.**  Pooled, the comparison flatters whichever arm resets: an assertion
recorded on a day forces that day's balance onto the number the owner typed, so
the gap closes to the cent whether or not the records underneath are right.
This project had already measured that trap one screen over --
:mod:`app.services.bank_agreement` scores the per-day RESIDUE rather than the
balance gap precisely because "a gap of zero over a non-zero residue is the
exact shape this report exists to stop reading as agreement", and 11 of 35 real
disagreements on this same account read as exact agreement in the gap (finding
**N-337**).  A pooled score here would repeat that, in favour of the arm that
resets.  So the days are partitioned and both halves are printed.

**Why it does not simply score `bank_agreement`'s residue instead, which would
be the obvious way to dodge the confound.**  The residue is
``the app's own rows - the bank's lines`` for a day: a MOVEMENT comparison, and
a reset changes no movement.  So it is blind to the difference between these two
arms by construction, and scoring both would return the same number for reasons
that have nothing to do with which is right -- a tautology wearing the shape of
evidence.  The confound is answered by PARTITIONING the level comparison, not by
swapping it for a metric that cannot see the subject.  (That producer also
returns nothing for an account with no imported lines, and production holds 0
statement imports, 0 bank lines and 0 matches -- finding **N-368**.)  This
script reads the bank's exported daily-balance file directly, which is the only
outside record that exists for the span today.

**Usage** (from the repository root, against a production-shaped clone -- never
the dev runtime database, which other sessions move)::

    DATABASE_URL=postgresql://.../shekel_xf3c \\
        .venv/bin/python tests/manual/measure_cutover_against_bank.py \\
        --account 1 --bank ~/Downloads/checking/2026_ytd_daily_balances.csv

The bank file is SECU's "daily balances" export: a two-column
``date,balance`` CSV under a short preamble, listing only days that carried
activity.  A quiet day's closing balance is therefore the last named day's,
carried forward, and this script does that carry rather than skipping the day --
skipping would drop exactly the days where the two arms differ most.

Reads only.  No writes, no commit, no fixture.
"""

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.scenario import Scenario
from app.services import balance_at
from app.services.cash_ledger import walk_cash_ledger

_ZERO_MONEY = Decimal("0.00")
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class _Bank:
    """One parsed daily-balance export: the days it names and their closings.

    ONE value rather than a ``{day: balance}`` beside a sorted key list,
    because every reader needs both and two loose arguments are the shape this
    project rules a defect rather than a contract (plan Section 8).

    Attributes:
        closings: ``{day: closing balance}`` -- one entry per day the file
            NAMES, which is every day that carried activity.
        named: The same days, ascending.
    """

    closings: "dict[date, Decimal]"
    named: "list[date]"

    @classmethod
    def read(cls, path: str) -> "_Bank":
        """Parse a SECU daily-balance export.

        Args:
            path: The CSV file.  A row that is not exactly ``date,balance``
                with a parseable ``MM/DD/YYYY`` date is preamble and is
                skipped.

        Returns:
            The :class:`_Bank`.
        """
        closings: "dict[date, Decimal]" = {}
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.reader(handle):
                if len(row) != 2:
                    continue
                try:
                    day = datetime.strptime(row[0], "%m/%d/%Y").date()
                except ValueError:
                    continue
                closings[day] = Decimal(row[1])
        return cls(closings=closings, named=sorted(closings))

    def closing_on(self, day: date) -> "Decimal | None":
        """Return the bank's closing balance FOR *day*.

        A quiet day is absent from the file, so its closing is the last named
        day's, carried forward.

        Args:
            day: The civil day to answer.

        Returns:
            The closing balance, or ``None`` for a day before the file begins.
        """
        prior = [named for named in self.named if named <= day]
        return self.closings[prior[-1]] if prior else None


def _score(label: str, diffs: "list[Decimal]") -> None:
    """Print one arm's day count, exact agreements, worst and mean error.

    Args:
        label: The row label.
        diffs: One signed ``rendered - bank`` per compared day.
    """
    if not diffs:
        print(f"{label:<46}{'no days':>7}")
        return
    count = len(diffs)
    exact = sum(1 for diff in diffs if diff == _ZERO_MONEY)
    worst = max(abs(diff) for diff in diffs)
    mean = (sum(abs(diff) for diff in diffs) / count).quantize(_CENT)
    print(f"{label:<46}{count:>5}{exact:>7}{worst:>12}{mean:>11}")


def _compare(account_id: int, bank_path: str) -> None:
    """Score both arms against the bank file and print the split table.

    Args:
        account_id: The cash account to value.
        bank_path: The SECU daily-balance export.

    Raises:
        SystemExit: When the account carries no balance assertion, so neither
            arm has an opening to build on.
    """
    bank = _Bank.read(bank_path)
    print(f"bank file: {len(bank.named)} days, "
          f"{bank.named[0]} .. {bank.named[-1]}")

    account = db.session.get(Account, account_id)
    scenario = db.session.execute(
        db.select(Scenario).filter_by(user_id=account.user_id, is_baseline=True)
    ).scalar_one()
    walk = walk_cash_ledger(account_id, scenario.id)
    if not walk.anchor_facts:
        raise SystemExit(f"account {account_id} has no balance assertion")

    # Read off the walk's PUBLIC facts rather than through the seam's assertion
    # replay: what this script needs of an assertion is its day and the balance
    # it declared, and neither is a correction.  Reaching for
    # ``balance_at._assertions`` would be a private-module import (W9910) buying
    # nothing -- ``anchor_facts[0]`` is the opening for the same reason the
    # replay's ``[0]`` is (``cash_anchor_facts`` loads business-date ascending).
    opening = walk.anchor_facts[0]
    opening_day = opening.observed_on
    prior_sources = sum(
        (fact.delta for fact in walk.source_facts
         if fact.settled_on <= opening_day),
        _ZERO_MONEY,
    )
    # The cutover's constant: what makes the opening day's sum-of-postings equal
    # the opening assertion.  Ruling R-GX stores this; here it is derived, which
    # is the same number by construction (plan step X-f3c-2).
    opening_equity = opening.anchor_balance - prior_sources
    context = balance_at.BalanceContext.build(
        user_id=account.user_id, as_of=max(bank.named),
    )

    print(f"opening {opening_day}: asserted {opening.anchor_balance}, sources "
          f"on or before it {prior_sources}, so opening equity "
          f"{opening_equity}")
    _report(_arms(account, context, walk, opening_equity, bank))


def _arms(
    account: Account, context, walk, opening_equity: Decimal, bank: _Bank,
) -> "dict[tuple[str, bool], list[Decimal]]":
    """Return each arm's signed ``rendered - bank`` per compared day.

    A day is compared when the bank names it OR the account carries an
    assertion on it; a bank-quiet day inside the span takes the prior closing
    (:meth:`_Bank.closing_on`).  The SPAN is derived here rather than passed --
    it is the account's opening assertion through the bank file's last day, and
    both ends are already in the two arguments that carry them.

    Args:
        account: The account being valued.
        context: The read pass, built at the bank file's last day.
        walk: Its :class:`~app.services.cash_ledger.CashLedgerWalk`.
        opening_equity: The cutover's constant.
        bank: The parsed export.

    Returns:
        ``{(arm, day carries an assertion): [diff, ...]}`` for both arms and
        both partitions.
    """
    arms: "dict[tuple[str, bool], list[Decimal]]" = {
        (arm, on_assertion): []
        for arm in ("today", "cutover") for on_assertion in (True, False)
    }
    assertion_days = {fact.observed_on for fact in walk.anchor_facts}
    day, last_day = walk.anchor_facts[0].observed_on, max(bank.named)
    while day <= last_day:
        if day in bank.closings or day in assertion_days:
            closing = bank.closing_on(day)
            on_assertion = day in assertion_days
            arms[("today", on_assertion)].append(
                balance_at.cash_balance_at(account, context, day) - closing,
            )
            settled = sum(
                (fact.delta for fact in walk.source_facts
                 if fact.settled_on <= day),
                _ZERO_MONEY,
            )
            arms[("cutover", on_assertion)].append(
                opening_equity + settled - closing,
            )
        day += timedelta(days=1)
    return arms


def _report(arms: "dict[tuple[str, bool], list[Decimal]]") -> None:
    """Print the split table, then the pooled figures it warns about.

    The pooled rows are printed LAST and labelled, rather than omitted: the
    number that reversed this step's order was the pooled one, and a reader
    checking that history has to be able to see it beside what de-confounds it.

    Args:
        arms: :func:`_arms`' output.
    """
    print(f"\n{'':<46}{'days':>5}{'exact':>7}{'worst':>12}{'mean':>11}")
    _score("today, days carrying an ASSERTION", arms[("today", True)])
    _score("cutover, days carrying an ASSERTION", arms[("cutover", True)])
    _score("today, days carrying NO assertion", arms[("today", False)])
    _score("cutover, days carrying NO assertion", arms[("cutover", False)])
    print()
    _score("today, POOLED (confounded -- see the docstring)",
           arms[("today", True)] + arms[("today", False)])
    _score("cutover, POOLED (confounded)",
           arms[("cutover", True)] + arms[("cutover", False)])


def main() -> None:
    """Parse the arguments, open an app context and print the comparison."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--account", type=int, required=True,
        help="the cash account id to value",
    )
    parser.add_argument(
        "--bank", required=True,
        help="path to the SECU daily-balance CSV export",
    )
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        _compare(args.account, args.bank)


if __name__ == "__main__":
    main()
