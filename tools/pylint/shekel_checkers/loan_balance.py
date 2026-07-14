"""Loan balance-source checker: ``shekel-loan-balance-source`` (W9905).

Flags passing a stored loan column (``original_principal`` /
``current_principal``) as the pre-first-payment / empty-schedule fallback to a
loan period-balance map producer. That fallback must be the resolver-derived
``current_balance``; a stored column makes a loan's projected balance leap to
its real value when the first payment lands -- the recurring net-worth defect
fixed in F-21 / Commit 19 and PR #44.
"""

from astroid import nodes

from pylint.checkers import BaseChecker

# The loan-balance producers in app/services/account_projection.py that take a
# BALANCE SEED -- the value reported when no schedule row precedes the target
# (a date before the first upcoming payment, or an empty / paid-off schedule).
# Each takes it as its third positional argument, keyword ``current_balance``.
#
# ``compute_loan_period_balance_map`` was the original offender and is now
# DELETED (the ledger owns every begun period; C2b of the fail-loud arc). The two
# FORWARD producers inherited its seed argument, so they inherit its hazard and
# join the fence: passing an origination amount to either of them reports a loan's
# ORIGINAL principal for every future date before its next payment, which is
# exactly the phantom net-worth jump W9905 exists to prevent.
_LOAN_BALANCE_MAP_FUNCS = frozenset({
    "balance_from_schedule_at_date",
    "forward_balance_at_date",
    "compute_forward_loan_period_balance_map",
})
_LOAN_BALANCE_ARG_INDEX = 2
_LOAN_BALANCE_ARG_KEYWORD = "current_balance"
# The two demoted, non-authoritative loan columns (app/models/loan_params.py):
# ``original_principal`` is immutable origination state and ``current_principal``
# is a non-authoritative seed. Neither is the live balance the resolver derives,
# so neither may be the fallback above.
_NON_AUTHORITATIVE_LOAN_BALANCE = frozenset(
    {"original_principal", "current_principal"},
)


def _is_loan_balance_map_call(node: nodes.Call) -> bool:
    """Return True if ``node`` calls a loan period-balance map producer.

    Matches the bare-name import form (``forward_balance_at_date(...)``)
    and the attribute form (``account_projection.balance_from_schedule_at_date(...)``)
    alike, mirroring ``_is_decimal_call``; name matching keeps the checker fast,
    and these identifiers are distinctive enough to carry no collision risk.
    """
    func = node.func
    if isinstance(func, nodes.Name):
        return func.name in _LOAN_BALANCE_MAP_FUNCS
    if isinstance(func, nodes.Attribute):
        return func.attrname in _LOAN_BALANCE_MAP_FUNCS
    return False


def _loan_balance_argument(node: nodes.Call) -> nodes.NodeNG | None:
    """Return the balance/fallback argument of a loan balance-map call, or None.

    The balance is the third positional argument or the ``current_balance``
    keyword; ``None`` when the call supplies neither (a ``*args`` or partial call
    the checker cannot statically inspect, which is not reported).
    """
    if len(node.args) > _LOAN_BALANCE_ARG_INDEX:
        return node.args[_LOAN_BALANCE_ARG_INDEX]
    for keyword in node.keywords or []:
        if keyword.arg == _LOAN_BALANCE_ARG_KEYWORD:
            return keyword.value
    return None


def _is_non_authoritative_loan_balance(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` reads a demoted loan column, not the live balance.

    Matches the attribute form ``params.original_principal`` and the bare-name
    parameter form ``original_principal`` / ``current_principal`` that fed the
    F-21 / PR #44 bug. A ``current_balance`` name or a ``.current_balance``
    attribute -- the resolver-derived value -- is the correct argument and is not
    matched.
    """
    if isinstance(node, nodes.Attribute):
        return node.attrname in _NON_AUTHORITATIVE_LOAN_BALANCE
    if isinstance(node, nodes.Name):
        return node.name in _NON_AUTHORITATIVE_LOAN_BALANCE
    return False


class ShekelLoanBalanceSourceChecker(BaseChecker):
    """Forbid a stored loan column where the resolver's current balance belongs."""

    name = "shekel-loan-balance-source"
    msgs = {
        "W9905": (
            "Loan balance-map fallback is a stored loan column "
            "(original_principal / current_principal); pass the resolver-derived "
            "current_balance instead",
            "shekel-original-principal-as-balance",
            "balance_from_schedule_at_date, forward_balance_at_date and "
            "compute_forward_loan_period_balance_map "
            "(app/services/account_projection.py) take the loan's CURRENT balance "
            "as the pre-first-payment / empty-schedule seed. The schedule is "
            "today-forward, so a period before the first upcoming payment -- and "
            "every period of a paid-off loan whose schedule is empty -- sits at "
            "today's balance. LoanParams.original_principal (immutable origination "
            "state) and current_principal (a demoted, non-authoritative seed) are "
            "NOT that balance; the resolver is (loan_resolver.resolve_loan -> "
            "LoanState.current_balance). Passing a stored column makes the loan "
            "leap down to its real balance the moment the first payment lands -- a "
            "phantom liability drop and net-worth jump of (original principal - "
            "current balance). This is the recurring defect fixed in F-21 / "
            "Commit 19 and PR #44; the fallback must come from the same resolver "
            "call that produced the schedule.",
        ),
    }

    def visit_call(self, node: nodes.Call) -> None:
        """Flag a loan balance-map call whose fallback argument is a stored column.

        ``node`` is every call expression; only a call to one of the seeded loan
        balance producers (:data:`_LOAN_BALANCE_MAP_FUNCS`) whose statically-readable
        balance argument reads ``original_principal`` / ``current_principal`` is
        reported.
        """
        if not _is_loan_balance_map_call(node):
            return
        balance_arg = _loan_balance_argument(node)
        if balance_arg is not None and _is_non_authoritative_loan_balance(
            balance_arg,
        ):
            self.add_message("shekel-original-principal-as-balance", node=node)
