"""
Shekel Budget App -- the savings-goal door: what a goal save may state.

The ONE set of refusals both goal routes apply (plan step credit_card:CC-5-5d).
``create_goal`` and ``update_goal`` each checked the account inline, and the
two checks were already different -- the create refused an archived account,
the edit did not, so an edit could move a goal onto an account the create
would never have accepted.  That is the shape of a copied write inheriting none
of its twin's refusals; the debt goals (rulings R-CC69..R-CC73, R-CC87, R-CC88,
R-CC90) add five more rules, so the rules live here once and both routes call
this.

A goal is a SAVINGS goal or a DEBT goal by its account's category (a
liability is a debt), never by a column of its own:

* **Every goal** is on an account the owner holds, and a save that chooses the
  account -- a create, or an edit that changes it -- chooses an ACTIVE one.
* **A goal moves only from one savings account to another** (ruling R-CC87).
  A debt goal's start is what its debt owed the day the goal was created, so
  a move onto a debt, off one, or to another debt would re-read that start
  from a day the goal was not about this debt.  The owner creates a new goal
  instead.
* **A savings goal** targets more than ``$0.00`` (ruling R-CC72; the table
  holds only ``>= 0``, because the table cannot see the account's category).
* **A debt goal** is a fixed dollar amount (R-CC69's premise), carries no
  per-period contribution (ruling R-CC90), and targets less than what the debt
  owes when the target is saved (R-CC69, "less than the current amount owed"),
  where "owes" is the figure its /savings tile shows (ruling R-CC88).  A
  ``$0.00`` target is a payoff goal (R-CC72).  An edit that leaves the target
  alone is not re-judged against today's balance, so a goal the debt has
  already passed can still be renamed.
* **A new goal on a card or other non-loan debt RECORDS its start** (ruling
  R-CC91, refining R-CC71): the figure its target was judged against,
  handed back in the :class:`GoalVerdict` for the route to write.  A loan goal
  records nothing; its start is re-read from the books.  A goal on a loan TYPE
  whose terms are not entered yet is refused (ruling R-CC93): the terms would
  later turn a recorded start into a loan's, read by the day.

No Flask imports: the routes build the read pass and flash the refusal.
"""

from dataclasses import dataclass
from decimal import Decimal

from app import ref_cache
from app.enums import GoalModeEnum
from app.extensions import db
from app.models.account import Account
from app.models.savings_goal import SavingsGoal
from app.services.account_category import is_liability_account
from app.services.balance_at import BalanceContext
from app.services.liability_sign import owed
from app.services.savings_dashboard_service import (
    is_configured_loan,
    tile_balance_on,
)

#: The refusal for an account that is not the owner's, does not exist, or is
#: archived when the save chooses it -- one message for all three, so the
#: response does not tell another owner's account from a missing one.
INVALID_ACCOUNT = "Invalid account."

#: Ruling R-CC87's refusal for an edit that moves a goal onto, off or between
#: debts.
MOVE_REFUSED = (
    "A goal can move only from one savings account to another.  To track a "
    "different debt, or to switch between savings and a debt, create a new "
    "goal instead."
)


@dataclass(frozen=True)
class GoalProposal:
    """A goal as it would stand after the save: the four fields the door judges.

    A parameter object rather than four arguments, and scalars rather than the
    form payload: a CREATE states every field, while an EDIT states only what
    it changes, so the route fills each field the edit leaves out from the
    goal as it stands, and the door judges the goal the save would leave
    behind -- never the half of it the form happened to submit.  (An edit
    cannot change ``is_active``: deleting a goal is its only writer, so a goal
    the door judged is the goal that stays active.)

    Attributes:
        account_id: The account the goal would be on.
        goal_mode_id: Its mode (``ref.goal_modes``).
        target_amount: Its stored dollar target -- ``None`` for an
            income-relative goal.
        contribution_per_period: Its manual per-period contribution, or
            ``None``.
    """

    account_id: int
    goal_mode_id: int
    target_amount: Decimal | None
    contribution_per_period: Decimal | None


@dataclass(frozen=True)
class GoalVerdict:
    """What the door decided about one goal save.

    A refusal, or leave to write -- and, for a NEW goal on a card or other
    non-loan debt, the start that goal must record (ruling R-CC91, which
    refines R-CC71: that debt's tile values it at its pay period's END, so a
    start re-read later would take in everything recorded in the rest of the
    period).  The figure is the one the target was just judged against, so
    the goal's start and the check that admitted it cannot differ.

    Attributes:
        refusal: The message to show the owner, or ``None`` when the save may
            be written.
        start_owed: What the route writes to
            :attr:`~app.models.savings_goal.SavingsGoal.start_owed` on a
            CREATE of a goal on a non-loan debt; ``None`` for every other save
            (a savings goal has no start, a loan goal re-reads its own, and an
            edit never moves a start -- a goal cannot change debts, R-CC87).
    """

    refusal: str | None = None
    start_owed: Decimal | None = None


def judge_goal_save(
    ctx: BalanceContext,
    proposal: GoalProposal,
    current: SavingsGoal | None = None,
) -> GoalVerdict:
    """Decide one goal save: refuse it, or let it be written (with its start).

    Args:
        ctx: The route's read pass: its ``user_id`` is the owner, and a debt
            goal's target is judged against its account's tile at its day.
        proposal: The goal as the save would leave it.
        current: The goal being edited, as it stands; ``None`` for a create.

    Returns:
        The :class:`GoalVerdict`.  Nothing is written here: the route writes
        and commits only when there is no refusal.
    """
    account = db.session.get(Account, proposal.account_id)
    chooses_account = (
        current is None or proposal.account_id != current.account_id
    )
    if (
        account is None
        or account.user_id != ctx.user_id
        or (chooses_account and not account.is_active)
    ):
        return GoalVerdict(refusal=INVALID_ACCOUNT)
    is_debt = is_liability_account(account)
    if (
        current is not None and chooses_account
        and (is_debt or is_liability_account(current.account))
    ):
        return GoalVerdict(refusal=MOVE_REFUSED)
    if is_debt:
        return _judge_debt_goal(ctx, account, proposal, current)
    return GoalVerdict(refusal=_refuse_savings_goal(proposal))


def _refuse_savings_goal(proposal: GoalProposal) -> str | None:
    """Return why a SAVINGS goal save is refused, or ``None``.

    Args:
        proposal: The goal as the save would leave it.

    Returns:
        The refusal for a FIXED goal with no target -- the create schema's own
        rule, which an edit's partial payload skips when it omits the mode --
        or a ``$0.00`` target (ruling R-CC72); else ``None``.
    """
    if proposal.goal_mode_id != ref_cache.goal_mode_id(GoalModeEnum.FIXED):
        return None
    if proposal.target_amount is None:
        return "Target amount is required for fixed-amount goals."
    if proposal.target_amount <= Decimal("0.00"):
        return "A savings goal's target must be above $0.00."
    return None


def _judge_debt_goal(
    ctx: BalanceContext,
    account: Account,
    proposal: GoalProposal,
    current: SavingsGoal | None,
) -> GoalVerdict:
    """Decide a DEBT goal save.

    Args:
        ctx: The route's read pass.
        account: The debt the goal is on.
        proposal: The goal as the save would leave it.
        current: The goal being edited, or ``None`` for a create.

    Returns:
        The refusal for the first rule the save breaks, in the order the form
        asks them; else leave to write, with the start a NEW goal on a
        non-loan debt records.  An edit that leaves the target alone reads no
        balance at all.
    """
    shape = _refuse_debt_goal_shape(proposal)
    if shape is not None:
        return GoalVerdict(refusal=shape)
    if current is not None and proposal.target_amount == current.target_amount:
        return GoalVerdict()
    is_loan = is_configured_loan(account, ctx)
    if account.account_type.has_amortization and not is_loan:
        # A loan TYPE with no terms yet reads like a card, so a goal on it
        # would record a start that the terms, entered later, turn into a
        # loan read by the day (the delta review's HIGH-1).  Refused until the
        # terms exist, so a loan type and a configured loan are one thing for
        # every account a goal is on, and the setup door meets no recorded
        # start (ruling R-CC93, amending R-CC91).
        return GoalVerdict(refusal=(
            f"Enter {account.name}'s loan terms before setting a goal on it."
        ))
    owed_today = owed(tile_balance_on(account, ctx, ctx.as_of, is_loan=is_loan))
    refusal = _refuse_debt_target(account, proposal.target_amount, owed_today)
    if refusal is not None or current is not None or is_loan:
        return GoalVerdict(refusal=refusal)
    return GoalVerdict(start_owed=owed_today)


def _refuse_debt_goal_shape(proposal: GoalProposal) -> str | None:
    """Return why a debt goal's FIELDS are refused, before any balance is read.

    Args:
        proposal: The goal as the save would leave it.

    Returns:
        The refusal for an income-relative mode (a debt goal is a fixed
        amount), a per-period contribution (ruling R-CC90) or a missing
        target; else ``None``.
    """
    if proposal.goal_mode_id != ref_cache.goal_mode_id(GoalModeEnum.FIXED):
        return "A goal on a debt is a fixed dollar amount: choose Fixed."
    if proposal.contribution_per_period is not None:
        return (
            "A goal on a debt has no per-period contribution: the debt's own "
            "planned payments are what move it."
        )
    if proposal.target_amount is None:
        return "A goal on a debt needs a target amount."
    return None


def _refuse_debt_target(
    account: Account, target_amount: Decimal, owed_today: Decimal,
) -> str | None:
    """Return why a debt goal's TARGET is refused against what the debt owes.

    Ruling R-CC69 ("less than the current amount owed"), read by the tile's
    rule (ruling R-CC88): *owed_today* is what the debt's /savings tile shows
    at the pass's day, so the goal and its tile start from one figure.

    Args:
        account: The debt.
        target_amount: The target the save states.
        owed_today: What the debt's tile shows it owing today.

    Returns:
        The refusal when the debt owes nothing, or the target is not below
        what it owes; else ``None``.
    """
    if owed_today <= Decimal("0.00"):
        return (
            f"{account.name} owes nothing today, so there is no balance for a "
            "goal to get under."
        )
    if target_amount >= owed_today:
        return (
            f"A goal on a debt must target less than it owes today: "
            f"{account.name} owes ${owed_today:,.2f}."
        )
    return None
