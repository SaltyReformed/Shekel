"""
Shekel Budget App -- Baseline Scenario Resolver

Single source of truth for "load the user's baseline scenario."
Every analytics service (budget_variance, calendar, spending_trend,
dashboard, retirement_dashboard, savings_dashboard, year_end_summary)
needs the user's baseline scenario as a starting point for queries
that must NOT spill into what-if scenarios.

Centralising the lookup in one helper closes the DRY violation that
pylint R0801 flagged across those services (Issue 1 of the
``docs/audits/security-2026-04-15/c-38-followups.md`` audit) and
gives a single place to extend the resolution logic (e.g. honouring
a future ``UserSettings.default_scenario_id`` override) without
chasing seven copies.
"""

from app.extensions import db
from app.models.scenario import Scenario


def get_baseline_scenario(user_id: int) -> Scenario | None:
    """Return the user's baseline scenario, or ``None`` if absent.

    Every user has exactly one baseline scenario (enforced by a
    partial unique index ``uq_scenarios_one_baseline`` that scopes the
    constraint to ``is_baseline IS TRUE``); the
    ``.filter_by(is_baseline=True).first()`` shape is safe.

    Callers MUST handle the ``None`` return.  In production every
    user has a baseline scenario created by
    ``auth_service.register_user`` at sign-up; ``None`` indicates a
    test fixture that did not seed one, or (in production) a freshly
    deleted user whose scenarios were cascaded out.  Analytics
    services treat ``None`` as an empty-report signal.

    Args:
        user_id: The user whose baseline scenario should be loaded.

    Returns:
        The baseline :class:`Scenario` instance, or ``None`` if the
        user has no baseline scenario.
    """
    return (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )
