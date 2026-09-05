"""Re-derive every SETTLED paycheck and account for the distance to its record.

Plan step **salary:S2** (``docs/plans/implementation_plan_salary.md`` section 4),
finding **N-442**: production's seven March-June 2026 paychecks were generated
and settled at ``$2,473.38`` and re-derive at ``$2,454.10`` with the calibration
removed, a ``-$19.28`` the ledger row recorded as UNACCOUNTED FOR.  The row's
named candidate was an engine change since 2026-03 that no audit trail records
-- ``balance:X-aw``'s deletion of the biweekly rounding residue.

**This harness asks the question the other way round.**  Rather than bisecting
the engine for a change that moves the figure, it prices the settled paychecks
under the stored calibration, under none, and under each historical one the
OPERATOR names on the command line, then reports which reproduces the record.
A calibration is the engine's only input that is both (a) a whole-paycheck
multiplier on all four withholding lines and (b) known to have been REPLACED
since generation -- ``system.audit_log`` carries the DELETE of the old row
beside the INSERT of the new one, with the deleted row's every column in
``old_data``.

**It is not a CENSUS of every calibration the data ever held, and an
adversarial review corrected a docstring that said it was**: it prices what it
is given.  A calibration deleted before the audit triggers were installed
leaves no recoverable trace at all -- ``calibration_overrides_id_seq`` on
production stands at 3 while rows 1 and 2 are both gone, and only row 2's
deletion is recorded.

**Why that is the stronger instrument.**  A bisect over ``app/`` can only
answer "which commit moved it" and cannot run at all below the migrations that
reshaped ``budget.pay_schedule``; this answers "which INPUT moved it" against
one tree, and an input that reproduces the record to the cent on every settled
row leaves the engine nothing left to explain.  If no calibration reproduces
it, the residue is what a bisect is then for, and the per-row residue printed
here is its target.

**It is READ-ONLY.**  Every calibration but the stored one is a
:class:`_RateCard` built in memory from figures passed on the command line or
read out of the audit log; nothing is assigned to an ORM attribute and nothing
is committed.  The stored state is priced through
:func:`app.services.income_service.project_profile` -- the ONE producer of a
profile's projection (ledger row **N-443**) -- and every state through
:func:`app.services.paycheck_calculator.project_salary` beneath it, which is
what the first CALLS.  **That is one producer invoked twice, not two doors**,
so the agreement it asserts grades the ARGUMENTS this harness assembles and
never the engine; see :func:`_grade_profile`.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://shekel_user:...@127.0.0.1:5432/shekel_s2 \\
        .venv/bin/python tests/manual/measure_settled_paycheck_derivation.py \\
            --rates 2026-03-26=0,0.0297972721,0.0551219512,0.0128899603 \\
            --rates 2026-08-27=0,0.0287420232,0.0535529616,0.0125225532 \\
            --json out.json

Each ``--rates`` names one calibration as
``LABEL=federal,state,ss,medicare``, the four ``effective_*_rate`` columns of
a ``salary.calibration_overrides`` row -- live, or recovered from an audit
row's ``old_data``.  The stored calibration and the no-calibration case are
always priced; ``--rates`` adds the historical ones a database no longer holds.
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from decimal import Decimal

from app import create_app, db
from app.enums import StatusEnum
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.services import income_service, paycheck_calculator
from app.services.pay_calendar import calendar_for
from app.services.payroll_basis import PayrollBasis
from app.services.tax_config_service import load_tax_configs_for_periods
from app import ref_cache

# The statuses that make a row's amount a RECORD rather than a plan.  A
# settled paycheck is worth what it recorded, so these are the rows whose
# stored figure a re-derivation is measured against.
SETTLED_STATUSES = (StatusEnum.RECEIVED, StatusEnum.DONE)


@dataclass(frozen=True)
class _RateCard:
    """A calibration's four effective rates, priced without being stored.

    The duck type :func:`~app.services.calibration_service.apply_calibration`
    and :func:`~app.services.paycheck_calculator.calculate_paycheck` consume
    between them: the four rates and ``is_active``.  Nothing else on a
    ``CalibrationOverride`` reaches the engine, which is why a historical
    calibration recovered from ``system.audit_log`` can be priced at all.

    Attributes:
        label: How this calibration is named in the report -- its pay-stub
            date, which is the one column that says WHICH stub it came from.
        effective_federal_rate: Federal withholding as a share of taxable pay.
        effective_state_rate: State withholding as a share of taxable pay.
        effective_ss_rate: Social Security as a share of gross.
        effective_medicare_rate: Medicare as a share of gross.
    """

    label: str
    effective_federal_rate: Decimal
    effective_state_rate: Decimal
    effective_ss_rate: Decimal
    effective_medicare_rate: Decimal
    is_active: bool = True


def _parse_rate_card(spec):
    """Build one :class:`_RateCard` from a ``LABEL=f,s,ss,med`` argument.

    Args:
        spec: The raw ``--rates`` value.

    Returns:
        The parsed :class:`_RateCard`.

    Raises:
        ValueError: The spec is not a label and exactly four rates.
    """
    label, _, rates = spec.partition("=")
    parts = [p.strip() for p in rates.split(",")]
    if not label or len(parts) != 4:
        raise ValueError(
            f"--rates wants LABEL=federal,state,ss,medicare; got {spec!r}"
        )
    return _RateCard(label, *(Decimal(p) for p in parts))


def _settled_paychecks(profile):
    """Return this profile's settled paycheck rows, oldest payday first.

    A salary profile's paycheck is the row set its own template authors
    (``routes.salary.profiles._paycheck_template``), so the template is the
    definition of "this is a paycheck" rather than a name match.

    Args:
        profile: The :class:`~app.models.salary_profile.SalaryProfile`.

    Returns:
        ``list[Transaction]`` in payday order, empty when the profile has no
        template or no settled row.
    """
    if profile.template is None:
        return []
    settled_ids = [
        ref_cache.status_id(status) for status in SETTLED_STATUSES
    ]
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.template_id == profile.template_id,
            Transaction.status_id.in_(settled_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )


def _price(basis, periods, configs_by_year, calibration):
    """Return ``{period_id: net_pay}`` for one calibration state.

    Args:
        basis: The owner's :class:`~app.services.payroll_basis.PayrollBasis`.
        periods: The periods to price.
        configs_by_year: ``{tax_year: config set}`` covering every period.
        calibration: The calibration to price under, or ``None``.

    Returns:
        ``dict[int, Decimal]`` keyed by ``budget.pay_periods.id``.
    """
    breakdowns = paycheck_calculator.project_salary(
        basis, periods, configs_by_year=configs_by_year,
        calibration=calibration,
    )
    return {b.period.period_id: b.earnings.net_pay for b in breakdowns}


def _grade_profile(profile, cards):
    """Price one profile's settled paychecks under every calibration state.

    Args:
        profile: The :class:`~app.models.salary_profile.SalaryProfile`.
        cards: The historical :class:`_RateCard` states to add beside the
            stored calibration and the no-calibration case.

    Returns:
        A record dict, or ``None`` when the profile has no settled paycheck.

    Raises:
        AssertionError: The direct ``project_salary`` call disagrees with
            :func:`income_service.project_profile` on the stored calibration,
            which would mean this harness is not measuring the app's own
            producer.
    """
    rows = _settled_paychecks(profile)
    if not rows:
        return None

    calendar = calendar_for(profile.user_id)
    basis = PayrollBasis(profile, calendar)
    periods = calendar.saved()
    configs_by_year = load_tax_configs_for_periods(
        profile.user_id, profile, periods,
    )

    # What this harness assembles, checked against what the app assembles.
    # `project_profile` CALLS `project_salary`, so this is one producer
    # invoked twice, NOT two independent producers -- it cannot grade the
    # engine, and an adversarial review of this step corrected a docstring
    # that claimed it did.  What it does grade is the four arguments, which
    # is where a harness standing outside a route gets things wrong: a
    # tax-config mapping resolved over the wrong window, a basis built on a
    # foreign calendar, a period list that is not the saved one.  Shown to
    # fire on an emptied `configs_by_year`; shown NOT to fire on a
    # single-year collapse, which is a no-op for these rows.
    door = {
        b.period.period_id: b.earnings.net_pay
        for b in income_service.project_profile(profile, calendar)
    }
    states = {"stored": _price(basis, periods, configs_by_year,
                              profile.calibration)}
    assert states["stored"] == door, (
        "this harness builds project_salary's arguments differently from "
        "income_service.project_profile, so every state it reports is priced "
        "off inputs the app does not use"
    )

    states["none"] = _price(basis, periods, configs_by_year, None)
    for card in cards:
        states[card.label] = _price(basis, periods, configs_by_year, card)

    return {
        "profile_id": profile.id,
        "user_id": profile.user_id,
        "annual_salary": str(profile.annual_salary),
        "stored_calibration": (
            None if profile.calibration is None
            else profile.calibration.pay_stub_date.isoformat()
        ),
        "paychecks": _paycheck_records(rows, states, calendar.saved_by_id()),
    }


def _paycheck_records(rows, states, saved_by_id):
    """Return one report record per settled paycheck, oldest payday first.

    Args:
        rows: The settled paycheck rows.
        states: ``{state name: {period_id: net_pay}}`` from :func:`_price`.
        saved_by_id: The calendar's ``{period_id: DerivedPeriod}`` map.

    Returns:
        ``list[dict]`` -- per row, the two stored figures, the generated
        figure each state is graded against, and the derivation and variance
        under every state.
    """
    records = []
    for row in sorted(
        rows, key=lambda r: saved_by_id[r.pay_period_id].start_date
    ):
        target, target_kind = _generated_figure(row)
        records.append({
            "transaction_id": row.id,
            "payday": saved_by_id[row.pay_period_id].start_date.isoformat(),
            "plan": None if row.estimated_amount is None
                    else str(row.estimated_amount),
            "record": str(row.settled_amount),
            "target": str(target),
            "target_kind": target_kind,
            "derived": {
                name: str(nets[row.pay_period_id])
                for name, nets in states.items()
            },
            "variance": {
                name: str(nets[row.pay_period_id] - target)
                for name, nets in states.items()
            },
        })
    return records


def _generated_figure(row):
    """Return the figure this row was GENERATED at, and where it was read from.

    A re-derivation is measured against what the engine produced for this
    paycheck when the row was written, and which column holds that figure
    depends on whether the row's plan is still STORED:

    * ``estimated_amount`` when the row carries one.  An override row keeps
      its authored figure, and so does every row on a tree below plan step
      ``balance:X-au-e``.
    * ``settled_amount`` when it does not.  ``X-au-e``'s migration NULLed the
      estimate of every row that declares its amount derived, so the
      generated figure is no longer in the row -- but for the paychecks this
      step is about the plan and the receipt agreed to the cent, which is a
      MEASUREMENT rather than an assumption: production still sits below that
      migration and carries ``estimated_amount = settled_amount = $2,473.38``
      on all seven (read 2026-09-04 from ``shekel-prod-db``).

    Grading a derived-plan row against its receipt is therefore the same
    comparison, and the report names which column each row used so the two
    are never silently pooled.

    **The fallback's precondition is a database CHECK rather than a guard
    here**, and an adversarial review of this step is why it says so.  The
    worry is a row that stores no plan and does not declare its amount derived
    either: its receipt would never have been a plan, and grading a derivation
    against it would pool two different questions silently, with the report's
    ``from`` column reading ``record`` and nothing saying it was wrong.  That
    row is UNREPRESENTABLE -- ``ck_transactions_amount_ownership`` is the
    biconditional ``(amount_source_id IS NULL) = (estimated_amount IS NOT
    NULL)``, so no estimate implies a declared source.  A guard was written
    here and DELETED once the constraint was read: a fence around a state the
    schema forbids is the thing this project's doctrine says to remove, and it
    would also have been unreachable code no run could exercise.

    What the constraint does NOT promise is that a row's plan and its receipt
    agreed -- four rows on this very profile differ by `$33.15` to `$38.05`.
    That is why the ``plan`` branch comes first and why the report prints which
    branch each row took.

    Args:
        row: The settled paycheck :class:`~app.models.transaction.Transaction`.

    Returns:
        ``(Decimal, str)`` -- the figure and the column it came from.
    """
    if row.estimated_amount is not None:
        return row.estimated_amount, "plan"
    return row.settled_amount, "record"


def _report(records):
    """Print the per-payday table and name the state that reproduces generation.

    Args:
        records: The per-profile records from :func:`_grade_profile`.

    Returns:
        The process exit status: 0 when some state reproduces every generated
        figure to the cent, 1 when none does or when nothing was graded.
    """
    if not records or not any(r["paychecks"] for r in records):
        # A run that graded nothing must not exit 0.  Pointed at the wrong
        # database, at one with no active profile, or at one whose paychecks
        # are all still projected, every loop below is empty and a silent
        # success is indistinguishable from a reproduction -- which is this
        # project's own "a green gate can be measuring nothing".
        print("GRADED NOTHING: no active salary profile with a settled "
              "paycheck was found. Check DATABASE_URL.")
        return 1
    status = 0
    for record in records:
        print(f"\n=== profile {record['profile_id']} "
              f"(user {record['user_id']}, salary {record['annual_salary']}, "
              f"stored calibration {record['stored_calibration']}) ===")
        states = list(record["paychecks"][0]["derived"])
        print("target = the figure the row was GENERATED at: its stored plan "
              "where one survives, else its receipt (see _generated_figure)")
        header = (f"{'payday':<12}{'plan':>10}{'record':>10}"
                  f"{'target':>10}{'from':>8}")
        for name in states:
            header += f"{name:>13}"
        print(header + "   variance vs target")
        for row in record["paychecks"]:
            line = (f"{row['payday']:<12}"
                    f"{row['plan'] or '--':>10}{row['record']:>10}"
                    f"{row['target']:>10}{row['target_kind']:>8}")
            for name in states:
                line += f"{row['derived'][name]:>13}"
            line += "   " + " ".join(
                f"{row['variance'][name]:>9}" for name in states
            )
            print(line)

        status = max(status, _verdict(record, states))
    return status


def _verdict(record, states):
    """Print which states reproduce this profile, and say what that MEANS.

    **Three outcomes, and telling them apart is the whole point of the step
    this harness was written for.**  One state reproducing every row says the
    inputs never moved.  Every row reproduced by SOME state while no single
    state reproduces them all says an input that varies over TIME was modelled
    as if it did not -- which is a dated-input finding and never an engine
    one, and it is what an undated calibration looks like from the outside.
    A row that NO state reproduces is the only outcome that leaves the engine
    something to answer for.

    A first draft printed the second case as "the residue is what an engine
    bisect must explain", which named the wrong subsystem for the exact
    finding this harness went on to make.

    Args:
        record: One profile record from :func:`_grade_profile`.
        states: The state names, in report order.

    Returns:
        0 when every settled paycheck is reproduced by some state, 1 when any
        row is reproduced by none.
    """
    rows = record["paychecks"]
    count = len(rows)
    exact = [
        name for name in states
        if all(Decimal(r["variance"][name]) == 0 for r in rows)
    ]
    if exact:
        print(f"\nREPRODUCES the generated figure on all {count} settled "
              "paychecks to the cent: " + ", ".join(exact))
        return 0

    by_row = {
        r["payday"]: [n for n in states if Decimal(r["variance"][n]) == 0]
        for r in rows
    }
    unexplained = [payday for payday, names in by_row.items() if not names]
    if unexplained:
        print(f"\nUNEXPLAINED: {len(unexplained)} of {count} settled "
              "paychecks are reproduced by NO state -- "
              + ", ".join(unexplained)
              + ". That residue is what an engine bisect must explain.")
        return 1

    print(f"\nNO SINGLE state reproduces all {count}, but EVERY row is "
          "reproduced by some state, so the input VARIED OVER TIME and the "
          "engine has nothing to answer for. The partition:")
    for name in states:
        covered = [payday for payday, names in by_row.items() if name in names]
        if covered:
            print(f"  {name:>12}: {len(covered)} row(s) -- "
                  f"{covered[0]} .. {covered[-1]}")
    return 0


def main(argv=None):
    """Grade every active salary profile's settled paychecks and report.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rates", action="append", default=[], metavar="LABEL=F,S,SS,MED",
        help="a historical calibration's four effective rates",
    )
    parser.add_argument("--json", metavar="PATH", help="write the raw record")
    args = parser.parse_args(argv)
    cards = [_parse_rate_card(spec) for spec in args.rates]

    # The tax calculator logs six DEBUG lines per paycheck, and this run
    # prices every period once per calibration state -- thousands of lines
    # around a table of eleven rows.  The report IS the output here.
    logging.disable(logging.INFO)

    app = create_app()
    with app.app_context():
        try:
            records = [
                record
                for profile in db.session.query(SalaryProfile)
                .filter(SalaryProfile.is_active.is_(True))
                .order_by(SalaryProfile.id)
                for record in [_grade_profile(profile, cards)]
                if record is not None
            ]
        finally:
            # Nothing here writes, and this is what says so even on an
            # exception path that autoflushed a lazy load.
            db.session.rollback()
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=1)
        print(f"wrote {len(records)} profile record(s) to {args.json}")
    return _report(records)


if __name__ == "__main__":
    sys.exit(main())
