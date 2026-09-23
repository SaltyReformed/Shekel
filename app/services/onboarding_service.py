"""
Shekel Budget App -- Onboarding Service

The facts behind the welcome checklist ``base.html`` draws for an owner whose
setup is incomplete, each one asked only when a template reads it.

**Asked on read, not on render** (ruling ``balance:R-BAL117``, plan step
``balance:X-x3``, closing ledger row **N-328**).  The context processor that
feeds the checklist used to run every probe on every template an owner's
request rendered, so five ``EXISTS`` queries ran on HTMX fragments that never
draw a layout.  Each fact is now a memoized attribute of :class:`OnboardingChecklist`,
so a render asks exactly the facts its template reads, and each one at most
once.  A fragment asks none.  A full page asks two when setup is complete
(``complete`` reads only the salary and recurring facts) and four when the
checklist is drawn.

**It holds no pay-period fact** (ruling ``balance:R-BAL116``, which supersedes
``R-DA``).  The checklist's "Generate pay periods" row is gone, and no writer
produces the state its unticked arm answered.  Every payday write goes through
``pay_period_write`` (``_apply`` and ``retire_paydays`` are the one delete
path): registration records at least one payday since plan step
``balance:X-ad-a`` (``pay_period_batch.PERIOD_BATCH_MIN``), reset and
regenerate record at least one inside the command that retires the old ones,
and truncate keeps the period it is told to keep through.  An owner an older
writer left at zero paydays (the legacy state
``pay_schedule_service.resolve_schedule`` names) still meets the grid's own
"Generate Pay Periods" button.  Asking R-DA's question instead ("does a
period cover today") from the layout derived the pay calendar a second time on
every money page, beside the page's own read pass, which the layout cannot
reach.  "No period covers today" is answered by the page that needs a period
(the grid's ``no_periods.html``, the dashboard's ``_no_period.html``), not by a
second surface on the same page that could disagree with it.
"""

from functools import cached_property

from sqlalchemy import exists

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate


def _exists(*criteria) -> bool:
    """Return whether any row matches *criteria* (one ``EXISTS`` query).

    The single door every checklist fact queries through, so the question
    "how many probes did this render run" has one place to be counted.

    Args:
        criteria: SQLAlchemy boolean clauses over one mapped table.

    Returns:
        ``True`` when at least one row matches.
    """
    return db.session.query(exists().where(*criteria)).scalar()


class OnboardingChecklist:
    """The welcome checklist's facts for one owner, each asked on first read.

    Built once per render by the ``inject_onboarding`` context processor.
    ``cached_property`` memoizes each fact on the instance, so a template that
    reads a fact twice (``complete`` and then the fact's own row) asks it once.
    """

    def __init__(self, user_id: int) -> None:
        """Bind the checklist to *user_id* without querying anything.

        Args:
            user_id: The owner the checklist describes.
        """
        self.user_id = user_id

    @cached_property
    def has_account(self) -> bool:
        """Whether the owner holds an active account."""
        return _exists(
            Account.user_id == self.user_id, Account.is_active.is_(True),
        )

    @cached_property
    def has_categories(self) -> bool:
        """Whether the owner holds any budget category."""
        return _exists(Category.user_id == self.user_id)

    @cached_property
    def has_salary(self) -> bool:
        """Whether the owner holds any salary profile."""
        return _exists(SalaryProfile.user_id == self.user_id)

    @cached_property
    def has_templates(self) -> bool:
        """Whether the owner holds any recurring transaction template."""
        return _exists(TransactionTemplate.user_id == self.user_id)

    @property
    def complete(self) -> bool:
        """Whether the checklist is done, which hides the banner.

        The two steps an owner must take after registering: a salary profile
        and a recurring transaction.  The account and the categories are
        provisioned at registration and never gated the banner.
        """
        return self.has_salary and self.has_templates
