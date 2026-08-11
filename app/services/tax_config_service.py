"""
Shekel Budget App -- Tax Config Service

Loads tax configuration objects (bracket sets, state configs, FICA)
required by the paycheck calculator.  Extracted from the salary route
to eliminate a route-to-route import and a duplicate copy in
chart_data_service.py.

**Which year's configuration applies to a given year is ONE rule and it lives
here** (:func:`resolve_tax_year`).  A user seeds configuration for the years the
app knows about -- 2025 and 2026 on production -- while pay periods run ~2 years
ahead, so most projected periods ask for a year that has none.  Answering that
with "no configuration" is not an option: the paycheck engine reads a missing
``fica_config`` as zero Social Security
(:func:`~app.services.tax_calculator.capped_social_security`, which documents
that arm for bootstrap), so an unresolved year silently inflates net pay by the
whole SS line.

The rule this module used to apply was "fall back to the CURRENT CALENDAR YEAR",
and it had a cliff the day the current year is itself unconfigured -- which is
every New Year, for every user, because configuration is seeded per year and
nothing seeds the next one.  Measured on a clone of production 2026-08-11: on
2027-01-01, with no write and no user action, 40 of 51 live-priced salary rows
change and the projected income over the horizon rises by **$8,460.50** (period
22 goes from ``NET 2,639.30`` with ``ss 205.19`` to ``NET 2,844.49`` with
``ss 0.00``).  Counting the 11 periods the grid's own rolling top-up creates
that same day it is **$10,914.93** over 51 of 62 rows.  The replacement rule
reads no clock at all, so no date can move a resolved figure.

**The read was not the whole exposure, which is why the direction of the fix
matters.**  Two writers would have made those figures permanent: a settle runs
``transaction_service._reconcile_cached_amount`` BEFORE the status flip and
writes the live figure into ``estimated_amount``, after which the row leaves
``income_service.live_projected_net``'s Projected-only candidate set and nothing
can repair it; and any salary, calibration or tax-config save runs
``routes.salary._helpers._regenerate_salary_transactions``, which rebuilds every
row from today forward.  A read-time defect with two write-back doors is a
storage defect on a delay.
"""

import logging
from dataclasses import dataclass

from app.extensions import db
from app.models.tax_config import (
    FicaConfig,
    StateChildDeduction,
    StateTaxConfig,
    TaxBracketSet,
)

logger = logging.getLogger(__name__)


def load_tax_configs(user_id, profile, tax_year):
    """Load the tax configuration stored for EXACTLY ``tax_year``.

    Queries TaxBracketSet, StateTaxConfig, and FicaConfig for a given
    tax year, matching the given salary profile's filing status and
    state code.

    **This is the exact-year primitive and it substitutes nothing.**  A year with
    no configuration returns three ``None``s, and every tax figure computed from
    that is wrong in a specific direction -- zero federal withholding, zero state
    withholding, and zero Social Security.  Unless you are asking "is this exact
    year configured", call :func:`load_tax_configs_for_year`, which answers
    "which configuration APPLIES to this year" and is what every consumer wants.

    **``tax_year`` is required, and it stopped being optional as the root-cause
    half of the New Year cliff** (see the module docstring).  The parameter used
    to default to ``date.today().year``, which put an unresolved clock read
    behind an omitted argument at six call sites; a defaulted year is exactly the
    shape that made the defect invisible at each of them.

    Args:
        user_id (int): The owning user's ID -- all tax configs are
            per-user so the query is ownership-scoped.
        profile (SalaryProfile): Must have ``filing_status_id`` and
            ``state_code`` attributes.
        tax_year (int): The tax year to load configs for.  No substitution
            is made for a year that has none.

    Returns:
        dict: Keys ``bracket_set``, ``state_config``, ``fica_config``.
            Each value is the matching model instance or ``None`` if no
            configuration exists for the requested year.
    """
    bracket_set = (
        db.session.query(TaxBracketSet)
        .filter_by(
            user_id=user_id,
            filing_status_id=profile.filing_status_id,
            tax_year=tax_year,
        )
        .first()
    )

    # T-P5: the state config is filing-status-keyed (the NC standard
    # deduction is status-specific), so the query filters by the profile's
    # filing status.  The withholding path resolves the profile's own
    # status; the analytics liability resolves the primary filer's.
    state_config = (
        db.session.query(StateTaxConfig)
        .filter_by(
            user_id=user_id,
            state_code=profile.state_code,
            tax_year=tax_year,
            filing_status_id=profile.filing_status_id,
        )
        .first()
    )

    fica_config = (
        db.session.query(FicaConfig)
        .filter_by(user_id=user_id, tax_year=tax_year)
        .first()
    )

    return {
        "bracket_set": bracket_set,
        "state_config": state_config,
        "fica_config": fica_config,
    }


@dataclass(frozen=True)
class ProfileTaxSeries:
    """Every tax configuration row a profile can resolve against, by kind and year.

    **Three INDEPENDENT year series, and their independence is the whole point.**
    An earlier draft of this module resolved ONE year for the profile from the
    UNION of the three tables and then loaded all three under it.  That is wrong
    in a way that moves money, because the loader needs each table to have its
    own row for that year: a year present in only one table became the resolved
    year for itself AND for every later year, and the other two lines silently
    became zero across the whole horizon.

    It is not a hypothetical.  The settings screen writes ``StateTaxConfig`` and
    ``FicaConfig`` for any year in ``[2000, 2100]``
    (:mod:`app.routes.salary.tax_config`), and NOTHING in ``app/`` ever creates a
    ``TaxBracketSet`` outside the signup seed -- so "one table has this year and
    the others do not" is precisely the state that screen produces.  Measured on
    a clone of production 2026-08-11 under the union rule: saving a single 2027
    state-tax row made 2028 resolve to 2027, dropping ``bracket_set`` and
    ``fica_config`` to ``None`` and Social Security to ``$0.00`` -- **+$216.63 a
    period** on a paycheck that was correct beforehand.  Resolving each kind
    against its own series makes that unrepresentable.

    Loading the whole series per kind rather than querying per year is what
    keeps the multi-year caller cheap: THREE queries for any horizon, with the
    year resolution then a pure in-memory pick.  The row counts are bounded by
    (years x filing statuses) and are single digits in practice -- eight bracket
    sets, eight state configs and two FICA rows on production.

    Attributes:
        bracket_sets: ``{tax_year: TaxBracketSet}`` for the profile's filing
            status.
        state_configs: ``{tax_year: StateTaxConfig}`` for the profile's state
            and filing status.
        fica_configs: ``{tax_year: FicaConfig}`` for the user.  FICA is keyed on
            the user ALONE -- it carries no filing status and no state -- which
            is a second reason the three cannot share one candidate set.
    """

    bracket_sets: "dict[int, TaxBracketSet]"
    state_configs: "dict[int, StateTaxConfig]"
    fica_configs: "dict[int, FicaConfig]"


def _series(model, **key) -> dict:
    """Return ``{tax_year: row}`` for one config kind under one query key.

    Args:
        model: The config model to load (``TaxBracketSet``, ``StateTaxConfig``
            or ``FicaConfig``).
        **key: The ownership / identity filter for this kind.  It must match
            what :func:`load_tax_configs` filters that same table by, or a year
            would count as configured here and load as ``None`` there.

    Returns:
        The kind's rows keyed by tax year.  A table's uniqueness constraint
        makes the key unique for a given filter, so no row can be lost to a
        collision.
    """
    return {
        row.tax_year: row
        for row in db.session.query(model).filter_by(**key).all()
    }


def profile_tax_series(user_id: int, profile) -> ProfileTaxSeries:
    """Load all three tax-configuration series for *profile*, in three queries.

    The candidate sets :func:`resolve_tax_year` picks from, and the reason that
    rule needs no clock: they are derived entirely from what the user has
    stored.

    Each kind is queried under the SAME key :func:`load_tax_configs` loads it by
    -- the bracket set by ``(user, filing_status)``, the state config by
    ``(user, state, filing_status)``, FICA by ``(user)``.  Keeping those in step
    is what makes a resolved year loadable: a year counted as configured under a
    looser key would resolve and then come back ``None``, which is the
    silent-zero-withholding failure this module exists to remove.  In
    particular a year configured for a DIFFERENT filing status is not a
    candidate here, so a married filer's year can never resolve onto a single
    filer's brackets.

    Args:
        user_id: The owning user's ID.
        profile (SalaryProfile): Supplies ``filing_status_id`` and
            ``state_code``.

    Returns:
        The profile's :class:`ProfileTaxSeries`; a kind the user has configured
        nothing for is an empty mapping.
    """
    return ProfileTaxSeries(
        bracket_sets=_series(
            TaxBracketSet,
            user_id=user_id, filing_status_id=profile.filing_status_id,
        ),
        state_configs=_series(
            StateTaxConfig,
            user_id=user_id,
            state_code=profile.state_code,
            filing_status_id=profile.filing_status_id,
        ),
        fica_configs=_series(FicaConfig, user_id=user_id),
    )


def resolve_tax_year(tax_year: int, configured: tuple[int, ...]) -> int | None:
    """Return which configured year's rules apply to ``tax_year``, or None.

    **The ONE substitution rule, and it reads no clock.**  The latest configured
    year at or before ``tax_year``; failing that -- ``tax_year`` predates every
    configured year -- the earliest configured year; failing that, ``None``,
    because nothing is configured and there is nothing to substitute.

    Latest-at-or-before is the rule because tax rules take effect and persist:
    the newest published brackets are the best available answer for a year not
    yet published, which is precisely the projected-period case.  Reaching
    FORWARD for a year that predates everything is the weaker arm and is stated
    as such -- it is an approximation for a historical year the user never
    configured, not a claim about that year's law.

    It is a pure function over ONE kind's candidate set, applied three times by
    :func:`_configs_from_series` rather than once over a shared set.  Purity is
    what lets the rule be tested without a database; per-kind application is
    what stops one table's row deciding another table's line (see
    :class:`ProfileTaxSeries`).

    **What it replaced, and why the replacement is a rule rather than a wider
    fallback.**  The previous rule substituted the CURRENT CALENDAR YEAR, which
    cannot answer for the year it is itself: on 2027-01-01 a 2027 period stopped
    falling back to 2026 and resolved to no configuration at all.  Widening that
    fallback -- "try the current year, then the one before" -- would move the
    cliff rather than remove it.  Deriving the answer from the configured set
    removes the class: every year resolves to a configured year whenever one
    exists, on every date.

    Args:
        tax_year: The tax year whose rules are wanted.
        configured: One kind's configured years, in any order.

    Returns:
        The configured year whose rules apply, or ``None`` when *configured*
        is empty.
    """
    at_or_before = [year for year in configured if year <= tax_year]
    if at_or_before:
        return max(at_or_before)
    return min(configured) if configured else None


def _pick(series: dict, tax_year: int):
    """Return the row from ONE kind's *series* whose rules apply to ``tax_year``.

    Args:
        series: That kind's ``{tax_year: row}`` mapping.
        tax_year: The tax year whose rules are wanted.

    Returns:
        The applicable row, or ``None`` when the kind has no rows at all.
    """
    resolved = resolve_tax_year(tax_year, tuple(series))
    return None if resolved is None else series[resolved]


def _configs_from_series(series: ProfileTaxSeries, tax_year: int) -> dict:
    """Resolve each kind in *series* independently for ``tax_year``.

    The shared body of :func:`load_tax_configs_for_year` and
    :func:`load_tax_configs_for_periods`, so the multi-year caller loads the
    series ONCE and every year after the first is pure computation.

    A substitution is logged at DEBUG rather than INFO because it is the
    STEADY STATE, not an event: every projected period beyond the last
    configured year resolves this way, on every read, forever.  What is not yet
    recorded anywhere a user can see is that a figure was computed against
    another year's rules -- an approximation the surfaces present as a plain
    dollar amount.

    Args:
        series: The profile's :class:`ProfileTaxSeries`.
        tax_year: The tax year whose rules are wanted.

    Returns:
        dict: Keys ``bracket_set``, ``state_config``, ``fica_config``; each
            value is the applicable row, or ``None`` when that kind has no rows.
    """
    configs = {
        "bracket_set": _pick(series.bracket_sets, tax_year),
        "state_config": _pick(series.state_configs, tax_year),
        "fica_config": _pick(series.fica_configs, tax_year),
    }
    substituted = {
        kind: config.tax_year
        for kind, config in configs.items()
        if config is not None and config.tax_year != tax_year
    }
    if substituted:
        logger.debug(
            "Tax year %d is unconfigured for %s; applying %s",
            tax_year, sorted(substituted), substituted,
        )
    return configs


def load_tax_configs_for_year(user_id, profile, tax_year):
    """Load the tax configuration that APPLIES to ``tax_year``.

    The resolving loader every consumer wants: each kind's own series decides
    which of ITS years applies (:func:`resolve_tax_year`).  Every surface that
    resolves per-year configs -- the recurrence engine (which GENERATES the
    stored grid net pay), the year-end summary, the tax report / withholding /
    liability services, and the salary projection, breakdown and dashboard paths
    -- goes through here, so the generated amount and the live recompute cannot
    diverge on which year's brackets and FICA wage base/cap apply (deep-hunt
    DH-#30).

    Args:
        user_id (int): The owning user's ID.
        profile (SalaryProfile): Supplies ``filing_status_id`` and
            ``state_code``.
        tax_year (int): The tax year whose rules are wanted.

    Returns:
        dict: Keys ``bracket_set``, ``state_config``, ``fica_config``.  A value
            is ``None`` only when the user has configured NO year for that kind
            -- never merely because *tax_year* itself is unconfigured, and never
            because a SIBLING kind is missing that year.
    """
    return _configs_from_series(
        profile_tax_series(user_id, profile), tax_year,
    )


def load_tax_configs_for_periods(user_id, profile, periods):
    """Resolve tax configs for every distinct tax year present in ``periods``.

    Returns a ``{tax_year: configs}`` mapping so a multi-year salary
    projection can apply each period's OWN year's rules -- the per-year
    resolution the recurrence engine already performs when generating the
    stored grid amounts (DH-#30).  The series load is THREE queries whatever the
    horizon, and each distinct year is then resolved in memory, so a full ~2-year
    span costs the same as a single-year read.

    **It took a ``fallback_year`` until 2026-08-11, and deleting that parameter
    is part of the fix rather than tidying.**  It existed so a call straddling
    New Year's could not pick two different fallbacks -- a real hazard under a
    rule that consulted the clock.  :func:`resolve_tax_year` consults no clock,
    so there is nothing left to pin: the same periods resolve the same way on
    every date, which is the stronger form of that guarantee.

    Args:
        user_id (int): The owning user's ID.
        profile (SalaryProfile): Supplies ``filing_status_id`` and
            ``state_code``.
        periods (list): PayPeriod objects; ``start_date.year`` selects the
            tax year for each.

    Returns:
        dict: ``{tax_year: {bracket_set, state_config, fica_config}}`` for
            each distinct year in ``periods`` (empty when ``periods`` is
            empty).
    """
    if not periods:
        return {}
    series = profile_tax_series(user_id, profile)
    return {
        year: _configs_from_series(series, year)
        for year in {period.start_date.year for period in periods}
    }


def load_state_child_deductions(user_id, state_code, tax_year, filing_status_id):
    """Load the state per-child deduction tiers for a state/year/filing status.

    Returns every :class:`~app.models.tax_config.StateChildDeduction` tier row
    for the given key (the NC AGI-tiered child deduction, T-P5), ordered by
    ``agi_min`` for readability.  The tier LOOKUP itself keys on ``agi_max``
    (see :func:`app.services.tax_calculator.resolve_child_deduction_per_child`),
    so the order here is only presentational.  A state/year/status with no
    seeded tiers (e.g. any non-NC state) yields an empty list, which the
    resolver treats as "no child deduction."

    Args:
        user_id (int): The owning user's ID (tiers are per-user seed rows).
        state_code (str): Two-letter state code.
        tax_year (int): The tax year to load tiers for.
        filing_status_id (int): The filing status the tiers apply to.

    Returns:
        list[StateChildDeduction]: The matching tier rows (possibly empty).
    """
    return (
        db.session.query(StateChildDeduction)
        .filter_by(
            user_id=user_id,
            state_code=state_code,
            tax_year=tax_year,
            filing_status_id=filing_status_id,
        )
        .order_by(StateChildDeduction.agi_min)
        .all()
    )
