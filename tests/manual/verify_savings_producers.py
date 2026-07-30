"""Dump every figure the /savings producer package answers, for a HEAD-vs-post diff.

Added at plan step X-t (2026-07-28) and kept, because the three steps after it
(X-u, X-v, X-w) all change these same producers.  ``tests/manual/verify_balance_baseline.py`` reads
the ``balance_at`` seam DIRECTLY, so for a step whose whole surface is ABOVE the
seam -- the savings-dashboard package, its serializers, and the dashboard debt
track -- it is byte-identical whatever the step did.  A free pass that reads as
proof.  This dumps the layer that step actually changes:

* ``compute_dashboard_data`` in full: every per-account projection field (the
  nested loan figures included), the grid grouping + subtotals, goals, the
  emergency metrics, the archived list, the debt summary with its payoff
  outlook and DTI block, the net-worth today figures, the 2-year series with
  its composition bands, the whole Horizon range with its milestones, and the
  sparklines;
* the NARROW producers (debt summary, goal progress, and the per-account
  cockpit balance cell for every account) plus the principal-paid fraction,
  which was a narrow producer of its own until plan step X-u;
* the ROUTE's serialized ``data-chart`` payload (the float boundary);
* ``dashboard_pulse_service.compute_tracks_section`` -- the debt track that
  carries the summary.

**NORMALIZED across the intended shape changes**, so a deliberate diff cannot
hide an accidental one.  Two so far, and each is a place where this ONE file
has to produce the same blob on the old tree and the new one:

* plan step X-t1 turned the per-account projection dict into a frozen
  ``AccountProjection`` carrying a nested loan value -- every reader here is
  dict-or-attribute tolerant and the loan half flattens to the same two keys
  either way;
* plan step X-u made the principal-paid fraction a ``DebtSummary`` field and
  deleted both the ``compute_debt_principal_progress`` producer and the
  ``DebtTrack`` wrapper -- so the fraction is read through
  :func:`_principal_fraction` / :func:`_narrow_fraction` / :func:`_full_build_fraction`,
  and the tracks section's ``debt`` normalizes whether it is the summary or a
  wrapper around it.

**A NORMALIZATION SHIM IS DELETED BY THE STEP AFTER THE ONE THAT NEEDED IT.**
Each of the above adds a branch that can never be taken on the current tree, and
they accumulate: X-v and X-w change these same producers, and a file whose
readers all tolerate three dead shapes is one that can no longer answer this
harness's own question (Section 7.2 -- can it SEE the code under test?).  So the
X-t1 tolerance goes when X-v ships, the X-u tolerance when X-w does, and this
paragraph goes with the last of them.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel \\
        .venv/bin/python tests/manual/verify_savings_producers.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel \\
        .venv/bin/python tests/manual/verify_savings_producers.py after.json
    diff before.json after.json

Run it against BOTH databases (``shekel`` is the dev runtime clone,
``shekel_f3_final`` the prod-shape one), before AND after, and use
``git worktree`` for the HEAD side -- never ``git checkout``, which reverts the
working tree and discards the change under test.

It answers "did anything move", never "is the answer right": two figures
identical here can both be wrong (finding N-69).  A step's PROOF is its firing
controls and its hand-computed oracles; this is the REGRESSION check beside
them, and it was shown FIRING on a planted one-cent defect in
``_project_one_account`` (56 diff lines, reaching the projections, the chart
payload, the net worth and the debt summary) before plan step X-t trusted it.

Like its ``verify_*`` siblings here it is deliberately outside pytest's
collection (``pytest.ini`` sets ``python_files = test_*.py``): it needs a
populated database chosen by the operator, not the seeded test template.
"""

import json
import pathlib
import sys
from decimal import Decimal

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` is not importable when this is run as
# ``.venv/bin/python tests/manual/verify_savings_producers.py`` -- the same
# bootstrap its sibling ``verify_balance_baseline.py`` carries, and for the same
# reason: this loads the app in-process because the figures it dumps are
# PRODUCER calls, not a rendered page.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.models.user import User
from app.routes.savings import _serialize_net_worth_chart, _serialize_sparklines
from app.services import dashboard_pulse_service, savings_dashboard_service
from app.services.scenario_resolver import get_baseline_scenario


def _get(obj, name):
    """Read *name* off a dict OR a dataclass, returning None when absent."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _money(value):
    """JSON-stable string for a Decimal (or None)."""
    if value is None:
        return None
    return f"{Decimal(value):.6f}"


def _date(value):
    """ISO string for a date (or None)."""
    return None if value is None else value.isoformat()


def _terms(terms):
    """Every field of the seam's LoanTerms."""
    if terms is None:
        return None
    return {
        "monthly_payment": _money(_get(terms, "monthly_payment")),
        "current_rate": _money(_get(terms, "current_rate")),
        "is_originated": _get(terms, "is_originated"),
    }


def _figures(figures):
    """Every field of the seam's LoanFigures."""
    if figures is None:
        return None
    return {
        "payoff_date": _date(_get(figures, "payoff_date")),
        "is_retired": _get(figures, "is_retired"),
        "is_paid_off": _get(figures, "is_paid_off"),
        "terms": _terms(_get(figures, "terms")),
    }


def _loan_halves(ad):
    """The loan figures + params of one projection, whichever shape carries them.

    Pre-X-t1 they are two optional dict keys; post-X-t1 they are the two fields
    of one nested value object.  Both normalize here, so the shape change is
    invisible in the blob and a FIGURE change is not.
    """
    loan = _get(ad, "loan")
    if loan is not None:
        figures, params = _get(loan, "figures"), _get(loan, "params")
    else:
        figures, params = _get(ad, "loan_figures"), _get(ad, "loan_params")
    return {
        "loan_figures": _figures(figures),
        "loan_original_principal": _money(_get(params, "original_principal")),
        "loan_params_account_id": _get(params, "account_id"),
    }


def _projection(ad):
    """One per-account projection, every field."""
    account = _get(ad, "account")
    interest = _get(ad, "interest_params")
    investment = _get(ad, "investment_params")
    return {
        "account_id": account.id,
        "account_name": account.name,
        "current_balance": _money(_get(ad, "current_balance")),
        "projected": {
            label: _money(value)
            for label, value in sorted((_get(ad, "projected") or {}).items())
        },
        "needs_setup": _get(ad, "needs_setup"),
        "is_liability": _get(ad, "is_liability"),
        "interest_apy": _money(_get(interest, "apy")),
        "investment_return": _money(_get(investment, "assumed_annual_return")),
        **_loan_halves(ad),
    }


def _outlook(outlook):
    """The three states of the payoff outlook."""
    if outlook is None:
        return None
    return {
        "all_clear_on": _date(_get(outlook, "all_clear_on")),
        "never_clears": _get(outlook, "never_clears"),
        "is_loan_free": _get(outlook, "is_loan_free"),
    }


def _debt_summary(summary):
    """The debt summary's money fields, its outlook and its DTI block.

    Deliberately NOT ``principal_paid_fraction``: plan step X-u added it, so a
    tree without it would dump ``null`` here and the intended shape change would
    read as a figure change.  It is dumped instead through the three top-level
    keys that carry it on BOTH trees -- ``narrow_principal_fraction``,
    ``full_build_principal_fraction`` and the tracks section's own copy -- which
    is where a real change to it shows up.
    """
    if summary is None:
        return None
    dti = _get(summary, "dti")
    return {
        "total_debt": _money(_get(summary, "total_debt")),
        "total_monthly_payments": _money(
            _get(summary, "total_monthly_payments"),
        ),
        "weighted_avg_rate": _money(_get(summary, "weighted_avg_rate")),
        "revolving_debt": _money(_get(summary, "revolving_debt")),
        "payoff_outlook": _outlook(_get(summary, "payoff_outlook")),
        "dti_ratio": _money(_get(dti, "ratio")),
        "dti_label": _get(dti, "label"),
    }


def _series(series):
    """The 2-year trend: periods, net, and every composition band.

    Read through :func:`_get` because plan step X-w3 turned the series and its
    period descriptors into frozen value objects (ruling R-CI); the tolerance is
    what lets this ONE file produce the same blob on the HEAD tree and the new
    one, and it goes when X-w5 deletes X-w's tolerances.
    """
    if series is None:
        return None
    return {
        "periods": [
            {
                "end_date": _date(_get(point, "end_date")),
                "period_index": _get(point, "period_index"),
            }
            for point in _get(series, "periods")
        ],
        "net": [_money(value) for value in _get(series, "net")],
        "current_index": _get(series, "current_index"),
        "composition": {
            band: [_money(value) for value in values]
            for band, values in sorted(_get(series, "composition").items())
        },
    }


def _horizon(horizon):
    """The Horizon range: dates, net, bands, and every milestone flag."""
    if horizon is None:
        return None
    return {
        "dates": [_date(value) for value in horizon["dates"]],
        "current_index": horizon["current_index"],
        "net": [_money(value) for value in horizon["net"]],
        "composition": {
            band: [_money(value) for value in values]
            for band, values in sorted(horizon["composition"].items())
        },
        "milestones": [
            {"date": _date(m["date"]), "label": m["label"]}
            for m in horizon["milestones"]
        ],
    }


def _today_figures(region):
    """The hero and its three chips, wherever this tree keeps them.

    Pre-X-w3 they are four keys spread flat on the region dict; post-X-w3 they
    are the four fields of the region's ``today`` value object.  Both normalize
    to the same four blob entries, so the shape change is invisible here and a
    FIGURE change is not.
    """
    today = _get(region, "today")
    source = region if today is None else today
    return {
        name: _money(_get(source, name))
        for name in (
            "net_worth", "total_assets", "total_liabilities", "liquid",
        )
    }


def _goal(datum):
    """One goal progress row, every money figure.

    Read through :func:`_get` because plan step X-w4 turned the row into a
    frozen ``GoalProgress`` (ruling R-CI); the tolerance goes with X-w's others.
    """
    trajectory = _get(datum, "trajectory")
    return {
        "goal_id": _get(datum, "goal").id,
        "current_balance": _money(_get(datum, "current_balance")),
        "progress_pct": _money(_get(datum, "progress_pct")),
        "remaining_periods": _get(datum, "remaining_periods"),
        "required_contribution": _money(_get(datum, "required_contribution")),
        "resolved_target": _money(_get(datum, "resolved_target")),
        "monthly_contribution": _money(_get(datum, "monthly_contribution")),
        "income_descriptor": _get(datum, "income_descriptor"),
        "has_salary_data": _get(datum, "has_salary_data"),
        "trajectory": None if trajectory is None else {
            "months_to_goal": trajectory["months_to_goal"],
            "projected_completion_date": _date(
                trajectory["projected_completion_date"],
            ),
            "pace": trajectory["pace"],
            "required_monthly": _money(trajectory["required_monthly"]),
        },
    }


def _principal_fraction(summary):
    """The principal-paid fraction of one summary, wherever this tree keeps it.

    Plan step X-u makes it a ``DebtSummary`` field; before it, the only producer
    of it was ``compute_debt_principal_progress`` and the summary had no such
    attribute.  Reading it tolerantly is what lets THIS FILE run against both
    trees and produce the same blob for the same data -- the X-t1 normalization
    one step over, and the reason the deliberate shape change cannot mask an
    accidental figure change.
    """
    return _get(summary, "principal_paid_fraction")


def _narrow_fraction(user_id, narrow_summary):
    """The narrow producers' principal-paid fraction, whichever exists here.

    Takes the already-computed narrow summary rather than fetching its own.
    It DID fetch its own until X-u's adversarial review counted the calls: this
    file measures finding N-109 (one render, two identical debt pipelines) and
    was running ``compute_debt_summary`` twice per user dump to do it.
    """
    legacy = getattr(
        savings_dashboard_service, "compute_debt_principal_progress", None,
    )
    if legacy is not None:
        return legacy(user_id)
    return _principal_fraction(narrow_summary)


def _full_build_fraction(user_id, summary, narrow_summary):
    """The full ``/savings`` build's principal-paid fraction, tolerantly.

    Post-X-u the full build's own ``debt_summary`` carries it; pre-X-u nothing
    on that path computed one, so the narrow figure stands in.  A summary that
    HAS the attribute is authoritative even when the value is ``None`` (the
    reachable no-loan-has-originated state), which is why this tests for the
    attribute rather than for a truthy value.
    """
    if summary is not None and hasattr(summary, "principal_paid_fraction"):
        return summary.principal_paid_fraction
    return _narrow_fraction(user_id, narrow_summary)


def _tracks(section):
    """The budget dashboard's tracks section (the debt track's composition)."""
    if section is None:
        return None
    out = {}
    for key, value in sorted(section.items()):
        if key == "debt":
            debt = value
            # Pre-X-u ``debt`` is a ``DebtTrack`` wrapping the summary; post-X-u
            # it IS the summary.  Both normalize to the same two entries.
            summary = _get(debt, "summary") if debt is not None else None
            out[key] = None if debt is None else {
                "summary": _debt_summary(summary if summary is not None else debt),
                "principal_paid_fraction": _money(_principal_fraction(debt)),
            }
        elif key == "goals":
            out[key] = [
                {
                    "name": row["name"],
                    "account_id": row["account_id"],
                    "current_balance": _money(row["current_balance"]),
                    "progress_pct": _money(row["progress_pct"]),
                    "target_amount": _money(row["target_amount"]),
                    "target_date": _date(row["target_date"]),
                    "pace": row["pace"],
                    "projected_completion_date": _date(
                        row["projected_completion_date"],
                    ),
                    "required_monthly": _money(row["required_monthly"]),
                    "monthly_contribution": _money(row["monthly_contribution"]),
                }
                for row in value
            ]
        else:
            out[key] = repr(value)
    return out


def _dump_user(user_id):
    """Every figure the savings package answers for one user.

    **A user with NO BASELINE SCENARIO is recorded as skipped, and that is a
    normalization, not an omission** (plan step X-v2).  Both real databases
    carry one such user -- the COMPANION, who owns no accounts, no pay periods
    and no scenario by design (``scripts/integrity_check`` DC-08 excludes that
    role for exactly this reason) and who cannot reach `/savings` at all,
    because ``require_owner`` 404s her off every balance surface.

    The two trees answer that state differently ON PURPOSE: before X-v2 this
    package returned a blob of fabricated ``$0.00`` figures for her, and after
    it the seam raises :class:`~app.exceptions.BaselineMissingError` for one
    application-level handler to answer.  Recording the SKIP keeps this one file
    producing the same blob on both trees, which is what makes the rest of the
    diff mean something -- and keeps the row visible, so a user who LOSES a
    baseline shows up as a change rather than vanishing.
    """
    if get_baseline_scenario(user_id) is None:
        return {"skipped": "no baseline scenario"}
    data = savings_dashboard_service.compute_dashboard_data(user_id)
    account_data = data["account_data"]
    # ONE narrow debt build per user, shared by the two keys that read it.
    narrow_summary = savings_dashboard_service.compute_debt_summary(user_id)
    return {
        "account_data": [_projection(ad) for ad in account_data],
        "grouped_accounts": {
            label: [_get(ad, "account").id for ad in group]
            for label, group in data["grouped_accounts"].items()
        },
        "group_subtotals": {
            label: _money(value)
            for label, value in data["group_subtotals"].items()
        },
        "property_equity": [
            {
                "account_id": _get(row, "account").id,
                "equity": _money(_get(_get(row, "equity"), "equity")),
                "ltv": _money(_get(_get(row, "equity"), "ltv")),
            }
            for row in data["property_equity"]
        ],
        "goal_data": [_goal(datum) for datum in data["goal_data"]],
        "emergency_metrics": {
            key: _money(value) if isinstance(value, Decimal) else value
            for key, value in sorted(data["emergency_metrics"].items())
        },
        "total_savings": _money(data["total_savings"]),
        "avg_monthly_expenses": _money(data["avg_monthly_expenses"]),
        "savings_accounts": [acct.id for acct in data["savings_accounts"]],
        # The archived rows became a frozen ``ArchivedAccount`` at plan step
        # X-w2 and the figure was RENAMED (ruling R-CH), so both spellings are
        # read here -- this file's own tolerance for the shape change it is
        # measuring, which is what lets it produce the same blob on the HEAD
        # tree and the new one.  It goes when the next step deletes X-w's
        # tolerances, exactly as this file's header requires.
        "archived_accounts": [
            {
                "account_id": _get(row, "account").id,
                "current_balance": _money(
                    _get(row, "last_anchor_balance")
                    if _get(row, "last_anchor_balance") is not None
                    else _get(row, "current_balance"),
                ),
            }
            for row in data["archived_accounts"]
        ],
        "debt_summary": _debt_summary(data["debt_summary"]),
        # The region became a frozen ``_NetWorthRegion`` at plan step X-w3, with
        # the four today figures COMPOSED on a ``today`` field where they used
        # to be spread flat (ruling R-CI).  ``_today_figures`` normalizes both,
        # so the blob is the same on either tree; X-w5 deletes it.
        "net_worth": {
            **_today_figures(data["net_worth"]),
            "series": _series(_get(data["net_worth"], "series")),
            "horizon": _horizon(_get(data["net_worth"], "horizon")),
        },
        "sparklines": {
            str(account_id): [_money(value) for value in values]
            for account_id, values in sorted(data["sparklines"].items())
        },
        # The presentation boundary: the exact JSON the canvas carries.
        "chart_json": json.loads(_serialize_net_worth_chart(data["net_worth"])),
        "sparkline_points": {
            str(key): value
            for key, value in sorted(
                _serialize_sparklines(data["sparklines"]).items(),
            )
        },
        # The narrow producers, each answering over its own restricted
        # load -- the "identical figures by construction" promise, measured.
        # There were four until plan step X-u deleted the second debt one.
        "narrow_debt_summary": _debt_summary(narrow_summary),
        "narrow_principal_fraction": _money(
            _narrow_fraction(user_id, narrow_summary),
        ),
        # The FULL build's fraction: post-X-u a field of the ``debt_summary``
        # above, pre-X-u a figure no full-build producer computed at all -- so
        # on the old tree this reads the narrow value, which is what the two
        # paths are asserted to share
        # (``TestDebtSummary::test_narrow_producer_matches_full_dashboard``
        # compares the whole value object).  Without this key the merged
        # field would reach ``/savings`` unmeasured by this harness.
        "full_build_principal_fraction": _money(
            _full_build_fraction(user_id, data["debt_summary"], narrow_summary),
        ),
        "narrow_goal_progress": [
            _goal(datum)
            for datum in savings_dashboard_service.compute_goal_progress(
                user_id,
            )
        ],
        "narrow_balance_cells": {
            str(_get(ad, "account").id): _cell(user_id, _get(ad, "account").id)
            for ad in account_data
        },
        "tracks_section": _tracks(
            dashboard_pulse_service.compute_tracks_section(user_id),
        ),
    }


def _cell(user_id, account_id):
    """The cockpit balance cell for one account, whichever shape it returns."""
    cell = savings_dashboard_service.compute_account_balance_cell(
        user_id, account_id,
    )
    if cell is None:
        return None
    return {
        "account_id": _get(cell, "account").id,
        "current_balance": _money(_get(cell, "current_balance")),
        "is_liability": _get(cell, "is_liability"),
    }


def main():
    """Dump every user's savings-producer figures to the given path."""
    out_path = pathlib.Path(sys.argv[1])
    app = create_app()
    with app.app_context():
        blob = {}
        for user in db.session.query(User).order_by(User.id).all():
            blob[str(user.id)] = _dump_user(user.id)
    out_path.write_text(
        json.dumps(blob, indent=2, sort_keys=True), encoding="utf-8",
    )
    print(f"wrote {out_path} ({len(blob)} users)")


if __name__ == "__main__":
    main()
