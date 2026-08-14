"""Grade the amount resolver against the answer the app gives today, on every row.

Plan step **X-au-b**'s oracle, and the proof its specification asks for: for
EVERY row in a database -- not a sample, not the contributing ones, not the
projected ones --
:func:`app.services.cash_ledger.resolve_transaction_amount` answers what the app
already answers, or the run fails naming the rows where it does not.

**"The answer the app gives today" is not single-valued, so this file names the
one it grades.**  ``routes/grid/page.py`` publishes ``live_estimated_amount`` as
``amount_overrides.get(txn.id, txn.estimated_amount)`` while
``dashboard_service.py:279`` reads the raw column, and that two-answer state IS
finding **N-224**.  The graded expression is the grid's, and it is the right one
because it is the ESTIMATE half -- the quantity the resolver answers and the one
plan step X-au-c makes NULLABLE.

**It is NOT the expression the balance surfaces fold, and a first draft of this
paragraph said it was.**  ``cash_ledger.income_amount`` evaluates
``override else effective_amount``; ``_expense_amount`` falls through to the
three-bucket entry reservation; ``balance_at._plan._planned_from_shadows`` reads
``live_cash.get(id, shadow.effective_amount)``.  ``effective_amount`` is
``actual ?? estimated`` with a zero for deleted and excluded rows, so for those
row classes the folded figure and the graded one differ BY DESIGN: this file
grades what a row's amount IS, and the fold composes that with a status and an
entered actual.  One row on the clone makes the difference visible -- a
Projected, non-override template row carrying an operator-typed
``actual_amount`` -- and what ``effective_amount`` answers for a row that owns
no amount is the ruling plan step X-au-c owes.

**AN AGREEMENT ORACLE ALONE CANNOT SEE THIS RESOLVER, and an adversarial review
proved it by deleting the resolver.**  With the whole body replaced by
``return txn.estimated_amount`` -- no dispatch, no rules, no refusals -- the
agreement pass reported *997 rows, 0 mismatches, OK*.  It has to: for the 946
rows that are not salary, the app's own answer IS the stored column, so
comparing a derived answer against it cannot distinguish "derived correctly"
from "read the column".  That is the harness question
``docs/plans/verification.md`` standard 3 asks -- *can it SEE the code under
test?* -- answered no, and a free pass that reads as proof is exactly what
standard 4's firing control exists to stop.

So this file runs TWO passes and the second is the one with teeth:

1. **AGREEMENT** -- every row's resolved amount equals the app's published
   answer.  This is the step's stated proof and it is necessary; it is not
   sufficient, for the reason above.
2. **INVARIANCE** -- every row's own stored amount column is perturbed in the
   session (never flushed, rolled back after), and every row is re-resolved.  A
   DERIVED row's answer must not move by a cent, and an OWN row's must move by
   exactly the perturbation.  A resolver that reads the column it is supposed to
   be replacing fails this pass on 750 rows, and the identity function above
   fails it on all 801 derived ones.

**What the second pass still cannot see, stated rather than left to be
rediscovered.**  It proves the answer is not the row's own column; it does not
prove which OTHER producer answered.  Two blind spots are measured and printed
by the census below rather than argued about:

* **the price series was MINED OUT of the column it is graded against**, so a
  TEMPLATE agreement re-attests X-au-a's backfill rather than testing this
  resolver independently.  Migration ``a9d3c15e7f42`` run-length-encodes
  ``estimated_amount`` over ``(template, amount)`` and stamps each run at its
  first ``due_date``; ``amount_as_of`` then returns that run's amount for every
  row in it, by construction.  Standard 2 of ``docs/plans/verification.md``
  forbids two producers that share code proving each other, and this pairing is
  one -- pass 2 below is what makes the comparison say something the backfill
  did not already guarantee, and it says only "the answer is not the row's own
  column";
* the price series' time dimension is observable on only **8 rows** of this
  clone (5 transactions and 3 transfers resolve to a SUPERSEDED version; exactly
  one transaction template and one transfer template hold more than one version,
  so for the other 42 the series and the ``default_amount`` scalar it replaced
  answer identically).  Reading the scalar instead of the series passes both
  passes here and is caught only by
  ``tests/test_services/test_amount_source.py``;
* the LOAN_PAYMENT rule prices **0 rows** here -- ``budget.loan_payment_settings``
  is empty on production -- so it is graded by the seeded fixtures in that same
  file and by nothing in this one.

**Where the two sides share a producer.**  For SALARY and LOAN_PAYMENT rows the
app's answer comes from ``live_amounts``, which since plan step X-au-b
is :func:`~app.services.cash_ledger.amount_basis` flattened -- the same function
the resolver reads.  What is graded for those rows is the DISPATCH: that the
rule claims exactly the rows the override map holds and no others (the census
prints both counts side by side).  Their ARITHMETIC is graded nowhere -- there
is no second producer of a net paycheck -- and saying so is the honest form of
it.  For TEMPLATE and TRANSFER rows the resolver reads the price series or the
parent transfer while the app reads the stored column, so pass 2 is what makes
that comparison mean something.

**Measured 2026-08-12** on a clone of production upgraded to ``a9d3c15e7f42``:
997 rows, 997 agreeing, 0 refusals, 801 derived rows invariant, 196 OWN rows
moving by exactly the perturbation.  The rule census was 452 TEMPLATE, 298
TRANSFER, 51 SALARY, 196 OWN, 0 LOAN_PAYMENT.  Every refusal arm counted zero,
which is why each one carries a firing control in the unit file rather than a
production instance here.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel_xaub \\
        .venv/bin/python tests/manual/verify_amount_resolver.py out.json

Exit status is 1 when any row disagrees, refuses, or fails the invariance pass,
so it is usable as a gate.  It opens a transaction and ROLLS IT BACK; it writes
nothing, and it never flushes the perturbation.  The JSON blob holds one record
per row for a before/after ``diff``; use ``git worktree`` for the HEAD side,
never ``git checkout``.

Like its ``verify_*`` siblings it is deliberately outside pytest's collection
(``pytest.ini`` sets ``python_files = test_*.py``): it needs a populated
database chosen by the operator, not the seeded test template.
"""

import json
import pathlib
import sys
from collections import Counter, defaultdict
from decimal import Decimal

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` is not importable when this is run as
# ``.venv/bin/python tests/manual/verify_amount_resolver.py`` -- the same
# bootstrap every sibling here carries.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.services import template_amount_service
from app.services.cash_ledger import (
    AmountRule,
    amount_basis,
    amount_rule,
    live_amounts,
    resolve_transaction_amount,
)

# The perturbation pass 2 applies to each row's own amount column.  Large enough
# that no real figure could absorb it by coincidence, and positive so
# ``ck_transactions_estimated_amount`` (``>= 0``) would still hold if it were
# ever flushed -- which it is not.
_NUDGE = Decimal("1000.00")

# The rules whose answer must NOT move when the row's own column does.
# LOAN_PAYMENT is deliberately absent: its MANUAL arm resolves through
# ``loan_payment_service._manual_shadow_amount``, which reads
# ``estimated_amount`` by design until plan step X-au-g cuts it over, so a
# manual payment is an expected mover and is reported rather than failed.
_DERIVED_RULES = frozenset({
    AmountRule.SALARY, AmountRule.TEMPLATE, AmountRule.TRANSFER,
})


def _money(value):
    """JSON-stable string for a Decimal (or None)."""
    return None if value is None else f"{Decimal(value):.2f}"


def _resolved_or_refusal(txn, basis):
    """Return ``(amount, refusal)`` for one row -- exactly one of them is None.

    Args:
        txn: The Transaction to price.
        basis: Its account's AmountBasis.

    Returns:
        The resolved Decimal and ``None``, or ``None`` and the refusal message.
    """
    try:
        return resolve_transaction_amount(txn, basis), None
    except AmountUnresolvable as exc:
        return None, str(exc)


def _superseded(txn):
    """Whether *txn*'s template prices it from a version a later one supersedes.

    The rows whose answer would CHANGE if the resolver read the
    ``default_amount`` scalar instead of the series -- ``set_amount`` keeps that
    scalar equal to the NEWEST version, so every other row is a coincidence
    rather than evidence.  Printed by the census so the run states how much of
    the series' time dimension it actually exercised.

    Args:
        txn: The Transaction to test.

    Returns:
        ``True`` when the template states a later price than the one this row
        resolves to.
    """
    if txn.template_id is None or txn.due_date is None:
        return False
    versions = template_amount_service.amount_versions(txn.template)
    if not versions:
        return False
    return any(version.effective_date > txn.due_date for version in versions)


def _grade_group(account, scenario_id, rows):
    """Grade every row of one (account, scenario) group -- both passes.

    Builds the basis ONCE for the group (the batching finding N-228 is about)
    and reads the app's published override map off it.  The map is fetched
    through ``live_amounts`` deliberately, because that is the surface the app
    reads and an oracle that reconstructed the merge here would be grading its
    own arithmetic.  Since plan step X-au-c2 it is a VIEW of the basis rather
    than a second producer call, so the oracle pays for one.

    Args:
        account: The group's Account.
        scenario_id: The group's scenario id.
        rows: Every Transaction in the group, whatever its status.

    Returns:
        A list of per-row record dicts.
    """
    basis = amount_basis(account.user_id, scenario_id, rows)
    overrides = live_amounts(basis)

    records = []
    for txn in rows:
        rule = amount_rule(txn)
        resolved, refusal = _resolved_or_refusal(txn, basis)
        records.append({
            "id": txn.id,
            "name": txn.name,
            "account_id": txn.account_id,
            "scenario_id": txn.scenario_id,
            "status_id": txn.status_id,
            "is_override": txn.is_override,
            "is_deleted": txn.is_deleted,
            "template_id": txn.template_id,
            "transfer_id": txn.transfer_id,
            "credit_payback_for_id": txn.credit_payback_for_id,
            "due_date": None if txn.due_date is None else txn.due_date.isoformat(),
            "rule": rule.value,
            "in_override_map": txn.id in overrides,
            "prices_from_a_superseded_version": _superseded(txn),
            "resolved": _money(resolved),
            "today": _money(overrides.get(txn.id, txn.estimated_amount)),
            "stored": _money(txn.estimated_amount),
            "refusal": refusal,
            "nudged": None,
        })

    # ── Pass 2: the invariance control ──────────────────────────────
    # Perturb each row's OWN column in the session -- never flushed, and the
    # caller rolls back -- and re-resolve.  ``no_autoflush`` is load-bearing:
    # the series and parent lookups issue SELECTs, and an autoflush would push
    # the perturbation at the database.
    with db.session.no_autoflush:
        for txn in rows:
            if txn.estimated_amount is None:
                continue
            txn.estimated_amount = txn.estimated_amount + _NUDGE
        for txn, record in zip(rows, records):
            nudged, _refusal = _resolved_or_refusal(txn, basis)
            record["nudged"] = _money(nudged)
    return records


def _load_groups():
    """Return ``{(account_id, scenario_id): [Transaction, ...]}`` for the whole DB.

    Every row, including soft-deleted, Cancelled and Credit ones: the resolver
    is TOTAL over rows, so an oracle that pre-filtered would grade only the
    shapes it already believed in.

    Returns:
        The grouped rows.
    """
    groups = defaultdict(list)
    for txn in db.session.query(Transaction).order_by(Transaction.id).all():
        groups[(txn.account_id, txn.scenario_id)].append(txn)
    return groups


def _invariance_failures(records):
    """Return the rows whose second pass contradicts their rule.

    A DERIVED row must be unmoved by a change to its own stored column; an OWN
    row must move by exactly the perturbation, because that column IS its
    answer.

    Args:
        records: Every per-row record.

    Returns:
        ``(failures, moved_own, held_derived)`` -- the offending records and the
        two counts the run reports.
    """
    failures, moved_own, held_derived = [], 0, 0
    for rec in records:
        if rec["refusal"] is not None or rec["nudged"] is None:
            continue
        rule = AmountRule(rec["rule"])
        before, after = Decimal(rec["resolved"]), Decimal(rec["nudged"])
        if rule is AmountRule.OWN:
            if after - before == _NUDGE:
                moved_own += 1
            else:
                failures.append((rec, "an OWN row ignored its own column"))
        elif rule in _DERIVED_RULES:
            if after == before:
                held_derived += 1
            else:
                failures.append((rec, "a DERIVED row moved with its own column"))
    return failures, moved_own, held_derived


def _report(records):
    """Print both passes and the census, and return the process exit status.

    Args:
        records: Every per-row record from :func:`_grade_group`.

    Returns:
        ``0`` when both passes are clean, else ``1``.
    """
    mismatched = [
        rec for rec in records
        if rec["refusal"] is None and rec["resolved"] != rec["today"]
    ]
    refused = [rec for rec in records if rec["refusal"] is not None]
    drifted = [
        rec for rec in records
        if rec["refusal"] is None and rec["resolved"] != rec["stored"]
    ]
    failures, moved_own, held_derived = _invariance_failures(records)

    print(f"rows graded: {len(records)}")
    print("rule census:")
    for rule, count in sorted(Counter(rec["rule"] for rec in records).items()):
        in_map = sum(
            1 for rec in records if rec["rule"] == rule and rec["in_override_map"]
        )
        print(f"  {rule:<13} {count:>5}   (in the override map: {in_map})")
    print(
        "rows whose answer comes from a SUPERSEDED version (the only rows on "
        "which the series' time dimension is observable): "
        f"{sum(1 for rec in records if rec['prices_from_a_superseded_version'])}"
    )

    drift = sum(
        abs(Decimal(rec["resolved"]) - Decimal(rec["stored"])) for rec in drifted
    )
    print(f"\npass 1 -- agreement with the published answer")
    print(f"  stored-vs-derived drift: {len(drifted)} rows / ${drift:,.2f}")
    for rec in drifted[:20]:
        print(
            f"    DRIFT id={rec['id']} {rec['name']!r} rule={rec['rule']} "
            f"stored={rec['stored']} resolved={rec['resolved']}"
        )
    print(f"  refusals: {len(refused)}")
    for rec in refused[:20]:
        print(f"    REFUSED id={rec['id']} rule={rec['rule']}: {rec['refusal']}")
    print(f"  mismatches: {len(mismatched)}")
    for rec in mismatched[:20]:
        print(
            f"    MISMATCH id={rec['id']} {rec['name']!r} rule={rec['rule']} "
            f"today={rec['today']} resolved={rec['resolved']}"
        )

    print(f"\npass 2 -- invariance under a ${_NUDGE} nudge to the row's own column")
    print(f"  DERIVED rows that held: {held_derived}")
    print(f"  OWN rows that moved by exactly the nudge: {moved_own}")
    print(f"  failures: {len(failures)}")
    for rec, why in failures[:20]:
        print(
            f"    {why}: id={rec['id']} {rec['name']!r} rule={rec['rule']} "
            f"{rec['resolved']} -> {rec['nudged']}"
        )

    if mismatched or refused or failures:
        return 1
    print(
        f"\nOK: all {len(records)} rows agree with the answer the app gives "
        f"today, and {held_derived} derived rows are invariant under a change "
        "to the column they are replacing"
    )
    return 0


def main(out_path=None):
    """Grade every row in the database and report.

    Args:
        out_path: Optional path to write the per-row JSON blob to.

    Returns:
        The process exit status.
    """
    app = create_app()
    with app.app_context():
        try:
            records = []
            for (account_id, scenario_id), rows in _load_groups().items():
                account = db.session.get(Account, account_id)
                records.extend(_grade_group(account, scenario_id, rows))
        finally:
            # Pass 2's perturbation lives only in the session; this is what
            # guarantees it never reaches the database even on an exception.
            db.session.rollback()
        if out_path is not None:
            pathlib.Path(out_path).write_text(
                json.dumps(records, indent=1), encoding="utf-8",
            )
            print(f"wrote {len(records)} records to {out_path}")
        return _report(records)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
