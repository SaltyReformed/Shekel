"""Project-specific pylint checkers for the Shekel budget app.

These AST checkers encode two financial-correctness rules from
``docs/coding-standards.md`` that generic pylint does not cover, so the rules
are enforced by a deterministic tool instead of relying on a reviewer (human or
LLM) to remember them. They are loaded through ``.pylintrc`` (``load-plugins``),
so every pylint invocation -- the per-edit hook, the Stop-hook full run, CI, and
pre-commit -- applies them automatically.

Rules implemented:

* ``shekel-decimal-from-float`` (W9901): flags ``Decimal`` constructed from a
  float, which inherits binary float imprecision. Monetary values must be built
  from strings.
* ``shekel-refname-compare`` (W9902): flags comparing a ``.name`` attribute
  against a string literal. Reference-table name columns are for display only;
  logic must key off IDs or enums.
* ``shekel-disable-rationale`` (W9903): flags any ``# pylint: disable=`` that
  lacks a standard ``Pylint:`` why-comment in the mandated location (the
  docstring for a def/class-scoped directive, a comment immediately above
  otherwise). Keeps every suppression auditable with one grep.
* ``shekel-bare-money-quantize`` (W9904): flags a ``.quantize()`` of a cents
  quantum (``Decimal("0.01")`` / ``CENTS`` / ``TWO_PLACES``) with no explicit
  rounding mode, which silently uses Python's default ``ROUND_HALF_EVEN``
  (banker's). ``app/utils/money.py`` mandates monetary rounding go through
  ``round_money`` (``ROUND_HALF_UP``); this locks the rule that the
  financial_calculations audit's E-26 / HIGH-04 remediation established.
* ``shekel-original-principal-as-balance`` (W9905): flags passing a stored loan
  column (``original_principal`` / ``current_principal``) as the
  pre-first-payment / empty-schedule fallback to
  ``compute_loan_period_balance_map`` or ``balance_from_schedule_at_date``. That
  fallback must be the resolver-derived ``current_balance``; a stored column
  makes a loan's projected balance leap to its real value when the first payment
  lands -- the recurring net-worth defect fixed in F-21 / Commit 19 and PR #44.
* ``shekel-balance-producer-bypass`` (W9906): flags any module OUTSIDE the
  ``app.services.balance_at`` seam and the engine cluster it composes from
  calling a balance producer (``balances_for``, ``balance_as_of_date``,
  ``calculate_balances`` / ``calculate_balances_with_interest``,
  ``compute_loan_period_balance_map``, ``balance_from_schedule_at_date``,
  ``build_account_balance_map``, ``base_account_balance_map``,
  ``account_balance_map_from_inputs``, ``_build_investment_balance_map``,
  ``_build_appreciation_balance_map``) directly. The seam owns all four per-kind
  balance-at-T boundary rules (cash / loan / investment / property) in ONE
  tested place; a consumer re-inventing that boundary is how the
  loan/investment balance-bug family kept recurring across files for months
  (``docs/audits/balance_architecture/``). The rich projection-detail
  primitives ``project_balance`` and ``resolve_loan`` / ``resolve_account_loan``
  are NOT producers (they return ProjectedBalance / LoanState detail the seam
  composes) and stay callable by the chart and loan-route consumers.

Deliberately NOT implemented as a checker: a blanket ``float()`` ban. The
codebase's real ``float()`` call sites are all legitimate (config timeouts that
are genuinely floats, and documented Decimal-to-float boundaries for Chart.js
JSON serialization). A static rule cannot distinguish a precision-losing
calculation from an end-of-pipeline serialization boundary without false
positives, so that judgment lives in the code-reviewer subagent instead.
"""

import io
import re
import tokenize

from astroid import nodes

from pylint.checkers import BaseChecker, BaseRawFileChecker

# Comparison operators where a name-vs-string-literal comparison is the
# reference-table antipattern. ``<`` / ``>`` on a name column are not the
# documented smell and are left alone.
_EQUALITY_OPS = frozenset({"==", "!="})
_MEMBERSHIP_OPS = frozenset({"in", "not in"})
# Attribute name of the reference-table display column. IDs are the logic key;
# this column is display-only (CLAUDE.md: "IDs for logic, strings for display").
_DISPLAY_COLUMN = "name"
# Constructor whose float argument loses precision.
_DECIMAL_CTOR = "Decimal"
# Builtin that produces a float; passing its result to Decimal is still a
# float-sourced Decimal even though the literal is hidden behind the call.
_FLOAT_BUILTIN = "float"

# The Decimal method that rounds to a fixed exponent.  A bare call (no rounding
# mode) falls back to the decimal context default, ROUND_HALF_EVEN.
_QUANTIZE_METHOD = "quantize"
# The keyword that selects the rounding mode explicitly.
_ROUNDING_KEYWORD = "rounding"
# Two-decimal "cents" quantum: the names the codebase uses for Decimal("0.01")
# (``CENTS`` from app/utils/money.py, plus the local ``TWO_PLACES`` /
# ``_TWO_PLACES`` redeclarations) and the literal itself.  A bare ``.quantize()``
# against one of these rounds MONEY; other quanta (one-decimal percentages, the
# five-decimal rate, the SWR slider) are not money and are not flagged.
_CENTS_QUANTUM_NAMES = frozenset({"CENTS", "TWO_PLACES", "_TWO_PLACES"})
_CENTS_QUANTUM_LITERAL = "0.01"

# Marker that prefixes every disable rationale.  Greppable via
# ``grep -rn "Pylint:" app/``; capitalized so it can never collide with pylint's
# own lowercase ``# pylint:`` pragma parser (which is case-sensitive).
_RATIONALE_MARKER = "Pylint:"
# Matches an inline ``# pylint: disable=<rules>`` directive inside a comment token
# and captures the comma-separated rule list.  ``enable=`` and ``disable-next=``
# are intentionally not matched: the codebase uses plain ``disable=`` only.
# ``#.*?`` (not ``#\s*``) so a directive behind prefix text in the same
# comment -- the historical ``# noqa: E402  pylint: disable=...`` combined
# form -- cannot evade the rationale gate: pylint honors the directive
# anywhere in the comment, so the checker must see everything pylint sees.
_DISABLE_RE = re.compile(r"#.*?pylint:\s*disable=([\w,\-]+)")

# Loan period-balance map producers in app/services/account_projection.py. Both
# take the loan's resolver-derived CURRENT balance as the pre-first-payment /
# empty-schedule fallback -- their third positional argument, keyword
# ``current_balance``.
_LOAN_BALANCE_MAP_FUNCS = frozenset(
    {"compute_loan_period_balance_map", "balance_from_schedule_at_date"},
)
_LOAN_BALANCE_ARG_INDEX = 2
_LOAN_BALANCE_ARG_KEYWORD = "current_balance"
# The two demoted, non-authoritative loan columns (app/models/loan_params.py):
# ``original_principal`` is immutable origination state and ``current_principal``
# is a non-authoritative seed. Neither is the live balance the resolver derives,
# so neither may be the fallback above.
_NON_AUTHORITATIVE_LOAN_BALANCE = frozenset(
    {"original_principal", "current_principal"},
)

# Balance producers (W9906): the functions that answer "what is account A's
# balance at time T?". Every screen must obtain a balance through the
# app.services.balance_at seam; a module outside the seam + engine cluster
# calling one of these directly re-invents the per-kind boundary rule the seam
# centralizes -- the recurrence generator behind the months-long
# loan/investment balance-bug family (docs/audits/balance_architecture/). The
# private (``_build_*``) producers are listed by their bare name: a consumer
# reaching one is already past the seam. NOT listed -- and so never flagged --
# are the rich projection-detail primitives the seam composes:
# ``project_balance`` / ``reverse_project_balance`` (return a ProjectedBalance
# with contribution/growth detail) and ``resolve_loan`` / ``resolve_account_loan``
# (return the full LoanState). Those are a different responsibility (rich detail,
# not a balance map) and stay callable by the chart and loan-route consumers by
# design.
#
# Two engine-cluster accessors that DO return a balance map are excluded by the
# same SRP line, and must NOT be added here:
#   * ``net_worth_kernel.investment_base_balance_map`` -- the cash-basis
#     PRE-GROWTH seed a forward growth projection compounds from. It is exposed
#     expressly so the investment / retirement / year-end growth consumers read
#     the seed WITHOUT calling the fenced cash producer directly; they display
#     the modeled balance via the seam's balance_map, and seed their charts off
#     this pre-growth map. Guarding it would false-flag those sanctioned
#     consumers (and break pylint 10.00).
#   * ``interest_by_period_for_account`` -- interest EARNED per period, not a
#     balance-at-T figure.
# The seam owns the balance to DISPLAY at time T; these own a projection INPUT.
# ``test_allows_investment_base_balance_map_from_consumer`` locks the exclusion.
_BALANCE_PRODUCERS = frozenset({
    "balances_for",
    "balance_as_of_date",
    "calculate_balances",
    "calculate_balances_with_interest",
    "compute_loan_period_balance_map",
    "balance_from_schedule_at_date",
    "build_account_balance_map",
    "base_account_balance_map",
    "account_balance_map_from_inputs",
    "_build_investment_balance_map",
    "_build_appreciation_balance_map",
})
# Modules allowed to call a balance producer directly: the balance_at seam plus
# the engine cluster it composes (the SOLID dependency direction -- consumers
# depend on the seam, the seam depends on these engines). Listed by their FULLY
# QUALIFIED module name, matched exactly or as a package prefix (see
# :func:`_in_balance_seam_cluster`). The full path -- not the basename -- is
# deliberate: a same-named module in another package (a hypothetical
# ``app/routes/balance_at.py``) must NOT be exempted, or the fence could be
# silently bypassed by a name collision (a false negative is the dangerous mode
# for a fence). Every gate runs pylint from the repo root, so a cluster module
# always resolves to ``app.services.<name>`` (``pylint app/``, the per-edit hook
# on a single file, and pre-commit all agree); the prefix match additionally
# keeps a cluster module's submodules inside the fence if one is ever split into
# a package.
_BALANCE_SEAM_MODULES = frozenset({
    "app.services.balance_at",
    "app.services.balance_resolver",
    "app.services.balance_calculator",
    "app.services.account_projection",
    "app.services.growth_engine",
    "app.services.net_worth_kernel",
})


def _is_decimal_call(node: nodes.Call) -> bool:
    """Return True if ``node`` calls ``Decimal`` (bare or ``decimal.Decimal``).

    Matched syntactically by name rather than by inference: ``Decimal`` is a
    distinctive identifier and name matching keeps the checker fast and
    false-positive-free. ``node`` is the call expression under inspection.
    """
    func = node.func
    if isinstance(func, nodes.Name):
        return func.name == _DECIMAL_CTOR
    if isinstance(func, nodes.Attribute):
        return func.attrname == _DECIMAL_CTOR
    return False


def _is_float_builtin_call(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a call to the builtin ``float`` constructor.

    Used to catch ``Decimal(float(x))``, where the float imprecision is laundered
    through an explicit ``float()`` call rather than written as a literal.
    """
    return (
        isinstance(node, nodes.Call)
        and isinstance(node.func, nodes.Name)
        and node.func.name == _FLOAT_BUILTIN
    )


def _is_float_literal(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a float constant, including ``-0.1`` forms.

    A unary plus/minus wrapping a float constant (``-0.1``) parses as a
    ``UnaryOp`` over a ``Const``; unwrap one level so the sign does not hide the
    float. Integer constants are intentionally NOT matched: ``Decimal(5)`` is
    exact, and flagging it would be noise (the imprecision is unique to floats).
    """
    target = node.operand if isinstance(node, nodes.UnaryOp) else node
    return isinstance(target, nodes.Const) and isinstance(target.value, float)


def _is_string_const(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a string literal constant."""
    return isinstance(node, nodes.Const) and isinstance(node.value, str)


def _is_display_name_attr(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` reads a ``.name`` attribute (e.g. ``status.name``).

    Matches the outermost attribute, so ``txn.status.name`` qualifies. Comparing
    such an attribute against a string literal is the reference-table antipattern;
    comparing it against a variable or column (``AccountType.name == data["name"]``)
    is legitimate and is not matched because the other operand is not a literal.
    """
    return isinstance(node, nodes.Attribute) and node.attrname == _DISPLAY_COLUMN


def _is_quantize_call(node: nodes.Call) -> bool:
    """Return True if ``node`` is an ``<expr>.quantize(...)`` method call.

    Matched syntactically on the method name (``Decimal.quantize`` is the only
    realistic ``.quantize`` in money code); name matching keeps the checker fast
    and avoids inference flakiness.
    """
    return (
        isinstance(node.func, nodes.Attribute)
        and node.func.attrname == _QUANTIZE_METHOD
    )


def _is_cents_quantum(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a cents quantum used for monetary rounding.

    Matches the literal ``Decimal("0.01")`` and the project's named cents
    constants (``CENTS`` / ``TWO_PLACES`` / ``_TWO_PLACES``, optionally qualified
    as ``money.CENTS``). A different quantum -- a one-decimal percentage
    ``Decimal("0.1")``, the five-decimal ``_RATE_PLACES``, the ``_PCT_QUANTUM``
    SWR slider -- is not money and is intentionally not matched, mirroring the
    audit's money-vs-percentage classification (06_dry_solid.md register (b)/(c)).
    """
    if isinstance(node, nodes.Name):
        return node.name in _CENTS_QUANTUM_NAMES
    if isinstance(node, nodes.Attribute):
        return node.attrname in _CENTS_QUANTUM_NAMES
    if _is_decimal_call(node) and node.args:
        first = node.args[0]
        return _is_string_const(first) and first.value == _CENTS_QUANTUM_LITERAL
    return False


def _has_explicit_rounding(node: nodes.Call) -> bool:
    """Return True if a ``.quantize()`` call selects its rounding mode explicitly.

    Either a second positional argument (the positional ``rounding`` parameter,
    ``x.quantize(CENTS, ROUND_HALF_UP)``) or the ``rounding=`` keyword
    (``x.quantize(CENTS, rounding=ROUND_HALF_UP)``) overrides the banker's
    default, so the call is not the bare-money antipattern.
    """
    if len(node.args) >= 2:
        return True
    return any(kw.arg == _ROUNDING_KEYWORD for kw in node.keywords or [])


def _is_loan_balance_map_call(node: nodes.Call) -> bool:
    """Return True if ``node`` calls a loan period-balance map producer.

    Matches the bare-name import form (``compute_loan_period_balance_map(...)``)
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


def _called_balance_producer(node: nodes.Call) -> str | None:
    """Return the guarded balance-producer name ``node`` calls, or ``None``.

    Matches the bare-name import form (``balances_for(...)``) and the attribute
    form (``balance_resolver.balances_for(...)``) alike, mirroring
    :func:`_is_loan_balance_map_call`; name matching keeps the checker fast, and
    these balance-map producers are distinctive enough to carry no realistic
    collision risk.  ``node`` is the call expression under inspection.
    """
    func = node.func
    if isinstance(func, nodes.Name) and func.name in _BALANCE_PRODUCERS:
        return func.name
    if isinstance(func, nodes.Attribute) and func.attrname in _BALANCE_PRODUCERS:
        return func.attrname
    return None


def _in_balance_seam_cluster(node: nodes.NodeNG) -> bool:
    """Return True if ``node``'s module is the seam or an engine-cluster module.

    Matches the enclosing module's fully-qualified name (``node.root().name``)
    against :data:`_BALANCE_SEAM_MODULES` exactly, or as a package prefix
    (``<module>.`` ...) so a cluster module later split into a package keeps its
    submodules inside the fence.  Matching the FULL path -- not the basename --
    means a same-named module in another package (a hypothetical
    ``app/routes/balance_at.py``) is NOT exempted, so the fence cannot be
    silently bypassed by a name collision.  An empty / unresolvable name matches
    nothing, so the producer call fails closed (is flagged): the safe direction
    for a fence.  The trailing dot in the prefix test is required so a sibling
    like ``app.services.balance_resolver_helpers`` does not match
    ``app.services.balance_resolver``.
    """
    name = node.root().name or ""
    return any(
        name == module or name.startswith(module + ".")
        for module in _BALANCE_SEAM_MODULES
    )


class ShekelMoneyChecker(BaseChecker):
    """Enforce monetary type-safety rules that generic pylint does not cover."""

    name = "shekel-money"
    msgs = {
        "W9901": (
            "Decimal constructed from a float; construct it from a string instead",
            "shekel-decimal-from-float",
            "Decimal(0.1) inherits binary float imprecision because 0.1 is not "
            "representable in binary floating point; the resulting Decimal is "
            "0.1000000000000000055511151231257827021181583404541015625. Build "
            'monetary Decimals from strings -- Decimal("0.1") -- per the Type '
            "Safety section of docs/coding-standards.md. Integer arguments are "
            "exact and are not flagged.",
        ),
        "W9904": (
            "Money value rounded with a bare .quantize(); round it through "
            "round_money() (ROUND_HALF_UP) instead",
            "shekel-bare-money-quantize",
            'A .quantize(Decimal("0.01")) call with no rounding= argument uses '
            "Python's decimal-context default ROUND_HALF_EVEN (banker's "
            "rounding), which disagrees with the project's ROUND_HALF_UP "
            "convention by a cent at every half-cent boundary. "
            "app/utils/money.py forbids reaching that mode implicitly: round "
            "monetary Decimals through round_money() (or the sanctioned "
            "round_money_ceiling()), the only auditable money boundary helpers "
            "(financial_calculations audit E-26 / HIGH-04). A quantize that "
            "passes an explicit rounding mode, or quantizes a non-cents quantum "
            "(a percentage or rate), is not flagged.",
        ),
    }

    def visit_call(self, node: nodes.Call) -> None:
        """Dispatch every call expression to the two monetary call checks.

        ``node`` is every call expression in the module; the two checks below
        match disjoint node shapes (a ``Decimal(...)`` constructor vs an
        ``<expr>.quantize(...)`` method call), so both run on every node.
        """
        self._check_decimal_from_float(node)
        self._check_bare_money_quantize(node)

    def _check_decimal_from_float(self, node: nodes.Call) -> None:
        """Flag ``Decimal(<float literal>)`` and ``Decimal(float(...))``.

        Only ``Decimal`` calls whose first argument is a float literal (optionally
        signed) or an explicit ``float()`` call are reported; string and integer
        arguments are exact and pass.
        """
        if not _is_decimal_call(node) or not node.args:
            return
        first = node.args[0]
        if _is_float_literal(first) or _is_float_builtin_call(first):
            self.add_message("shekel-decimal-from-float", node=node)

    def _check_bare_money_quantize(self, node: nodes.Call) -> None:
        """Flag ``<money>.quantize(<cents>)`` with no explicit rounding mode.

        Such a call rounds money with the banker's default; only cents quanta
        are matched (a percentage/rate quantum is not money), and a call that
        already passes a rounding mode is left alone.
        """
        if not _is_quantize_call(node) or not node.args:
            return
        if _is_cents_quantum(node.args[0]) and not _has_explicit_rounding(node):
            self.add_message("shekel-bare-money-quantize", node=node)


class ShekelRefNameChecker(BaseChecker):
    """Forbid logic that compares reference-table display names to string literals."""

    name = "shekel-refname"
    msgs = {
        "W9902": (
            "Comparison of a .name attribute against a string literal; "
            "key logic off IDs or enums, not display names",
            "shekel-refname-compare",
            "Reference-table 'name' columns are for display only (CLAUDE.md: "
            '"IDs for logic, strings for display only. NEVER compare against '
            'string name columns"). A literal comparison such as '
            "status.name == \"Projected\" silently breaks if the display label is "
            "renamed and bypasses the cached-enum model in app/enums.py / "
            "app/ref_cache.py. Compare the *_id column or the enum constant. "
            "Comparing .name against a variable or column is not flagged.",
        ),
    }

    def visit_compare(self, node: nodes.Compare) -> None:
        """Flag ``<expr>.name`` compared to a string literal via ==, !=, in, not in.

        ``node`` is every comparison expression. astroid stores a comparison as a
        left operand plus a list of ``(operator, operand)`` pairs (chained
        comparisons such as ``a == b == c`` produce several pairs); each adjacent
        operand pair is inspected. Only string-literal operands trigger a report,
        so ``AccountType.name == data["name"]`` (a non-literal right side) passes.
        """
        operands = [node.left] + [operand for _, operand in node.ops]
        operators = [operator for operator, _ in node.ops]
        for index, operator in enumerate(operators):
            left = operands[index]
            right = operands[index + 1]
            if operator in _EQUALITY_OPS and self._is_name_literal_equality(left, right):
                self.add_message("shekel-refname-compare", node=node)
                return
            if operator in _MEMBERSHIP_OPS and self._is_name_literal_membership(left, right):
                self.add_message("shekel-refname-compare", node=node)
                return

    @staticmethod
    def _is_name_literal_equality(left: nodes.NodeNG, right: nodes.NodeNG) -> bool:
        """Return True if one side reads ``.name`` and the other is a string literal.

        Order-independent so both ``status.name == "X"`` and ``"X" == status.name``
        are caught.
        """
        return (_is_display_name_attr(left) and _is_string_const(right)) or (
            _is_display_name_attr(right) and _is_string_const(left)
        )

    @staticmethod
    def _is_name_literal_membership(left: nodes.NodeNG, right: nodes.NodeNG) -> bool:
        """Return True for ``<expr>.name in (<string literals>)`` and ``not in``.

        The membership form lists allowed display labels (``status.name in
        ("done", "credit")``); flagged when the left side reads ``.name`` and the
        right side is a literal collection containing at least one string.
        """
        if not _is_display_name_attr(left):
            return False
        if isinstance(right, (nodes.Tuple, nodes.List, nodes.Set)):
            return any(_is_string_const(element) for element in right.elts)
        return _is_string_const(right)


class ShekelDisableRationaleChecker(BaseRawFileChecker):
    """Require every ``# pylint: disable=`` to carry a standard why-comment.

    ``docs/coding-standards.md`` mandates a rationale in a fixed location and
    format so every suppression is auditable with one grep (``Pylint:``):

    * Definition-scoped (the directive sits on a ``def``/``class`` line -- the
      ``too-many-*`` and ``too-many-instance-attributes`` smells): the rationale
      is a ``Pylint:`` note in that symbol's docstring.
    * Statement-scoped (any other line -- ``broad-except``, ``protected-access``,
      ``import-outside-toplevel``): the rationale is a ``# Pylint:`` comment
      immediately above the disabled line.

    Either way the rationale must name every rule the directive disables.  This
    checker enforces marker presence, location, and rule-naming; the
    ``(<count>/<limit>)`` shape is a documented convention, not machine-checked.
    """

    name = "shekel-disable-rationale"
    msgs = {
        "W9903": (
            "pylint disable has no standard rationale: add a ``Pylint:`` note "
            "naming %s %s",
            "shekel-disable-rationale",
            "Every ``# pylint: disable=`` must carry a why-comment in the "
            "standard location and format (docs/coding-standards.md): a "
            "``Pylint:`` note in the docstring when the directive is on a "
            "def/class line, or a ``# Pylint:`` comment immediately above the "
            "line otherwise, naming each disabled rule. One grep for ``Pylint:`` "
            "must then surface a justification for every suppression.",
        ),
    }

    def process_module(self, node: nodes.Module) -> None:
        """Flag every disable directive in the module lacking a standard rationale.

        ``node`` is the astroid module. The source is tokenized to locate comment
        tokens -- so a ``# pylint: disable=`` written inside a string literal is
        never matched -- and the AST is walked to map each def/class line to its
        docstring.
        """
        try:
            with node.stream() as stream:
                content = stream.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return

        comment_only: dict[int, str] = {}
        disables: list[tuple[int, list[str]]] = []
        try:
            for tok_type, tok_str, start, _end, line in tokenize.generate_tokens(
                io.StringIO(content).readline,
            ):
                if tok_type != tokenize.COMMENT:
                    continue
                lineno, col = start
                if line[:col].strip() == "":
                    comment_only[lineno] = tok_str
                match = _DISABLE_RE.search(tok_str)
                if match:
                    disables.append(
                        (lineno, [r for r in match.group(1).split(",") if r]),
                    )
        except tokenize.TokenError:
            return

        if not disables:
            return

        def_lines = self._definition_docstrings(node)
        for lineno, rules in disables:
            if lineno in def_lines:
                text = def_lines[lineno] or ""
                hint = "in the docstring"
            else:
                text = self._comment_block_above(comment_only, lineno)
                hint = "in a comment immediately above"
            if self._rationale_ok(text, rules):
                continue
            self.add_message(
                "shekel-disable-rationale",
                line=lineno,
                args=(", ".join(rules), hint),
            )

    @staticmethod
    def _definition_docstrings(node: nodes.Module) -> dict[int, str | None]:
        """Map each def/class signature line to its docstring (or ``None``).

        Keyed by ``fromlineno`` -- the ``def``/``class`` line (decorators
        excluded), which is where a definition-scoped disable directive sits.
        """
        defs = node.nodes_of_class(
            (nodes.FunctionDef, nodes.AsyncFunctionDef, nodes.ClassDef),
        )
        return {
            definition.fromlineno: (
                definition.doc_node.value if definition.doc_node is not None else None
            )
            for definition in defs
        }

    @staticmethod
    def _comment_block_above(comment_only: dict[int, str], lineno: int) -> str:
        """Join the contiguous comment-only lines immediately above ``lineno``.

        Stops at the first non-comment or blank line, so only an adjacent
        rationale block counts -- a comment separated from the directive by code
        or a blank line does not.
        """
        collected: list[str] = []
        cursor = lineno - 1
        while cursor in comment_only:
            collected.append(comment_only[cursor])
            cursor -= 1
        return "\n".join(collected)

    @staticmethod
    def _rationale_ok(text: str, rules: list[str]) -> bool:
        """Return True if ``text`` carries the marker and names every rule."""
        if _RATIONALE_MARKER not in text:
            return False
        return all(rule in text for rule in rules)


class ShekelLoanBalanceSourceChecker(BaseChecker):
    """Forbid a stored loan column where the resolver's current balance belongs."""

    name = "shekel-loan-balance-source"
    msgs = {
        "W9905": (
            "Loan balance-map fallback is a stored loan column "
            "(original_principal / current_principal); pass the resolver-derived "
            "current_balance instead",
            "shekel-original-principal-as-balance",
            "compute_loan_period_balance_map and balance_from_schedule_at_date "
            "(app/services/account_projection.py) take the loan's CURRENT balance "
            "as the pre-first-payment / empty-schedule fallback. The schedule is "
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

        ``node`` is every call expression; only a call to one of the two loan
        balance-map producers whose statically-readable balance argument reads
        ``original_principal`` / ``current_principal`` is reported.
        """
        if not _is_loan_balance_map_call(node):
            return
        balance_arg = _loan_balance_argument(node)
        if balance_arg is not None and _is_non_authoritative_loan_balance(
            balance_arg,
        ):
            self.add_message("shekel-original-principal-as-balance", node=node)


class ShekelBalanceSeamChecker(BaseChecker):
    """Forbid obtaining an account balance outside the balance_at seam.

    Every screen must read an account's balance-at-T through
    ``app.services.balance_at`` -- the single seam that owns all four per-kind
    boundary rules (cash / loan / investment / property) in ONE tested place.
    A module outside the seam and the engine cluster it composes calling a
    balance producer directly re-invents that boundary; that re-invention is how
    the loan/investment balance-bug family kept recurring across different files
    for months (docs/audits/balance_architecture/). This checker is the
    deterministic fence Level 1 adds: the seam + engine cluster may call the
    producers (they compose each other), and everything else must depend on the
    seam.
    """

    name = "shekel-balance-seam"
    msgs = {
        "W9906": (
            "Balance producer '%s' called outside the balance_at seam; obtain "
            "balances through app.services.balance_at instead",
            "shekel-balance-producer-bypass",
            "app.services.balance_at is the single seam through which every "
            "screen must obtain an account's balance over time (balance_map / "
            "build_maps / balance_at, plus the cash-flow views cash_balance_map "
            "/ cash_balance_at). Six producers historically answered 'what is "
            "account A's balance at time T?', and the three recompute-at-read "
            "kinds (loan, investment, property) each bolted on their own "
            "pre-first-data-point boundary rule; every new surface re-invented "
            "that boundary and shipped a balance bug at least once "
            "(docs/audits/balance_architecture/). The seam centralizes all four "
            "per-kind rules, so consumers (routes, savings, year-end, "
            "dashboards) must depend on it, never on a producer directly -- the "
            "SOLID dependency direction consumers -> seam -> engines. Only the "
            "seam and the engine cluster it composes (balance_resolver, "
            "balance_calculator, account_projection, growth_engine, "
            "net_worth_kernel) may call a producer. The rich projection-detail "
            "primitives project_balance and resolve_loan / resolve_account_loan "
            "are NOT producers and are not flagged -- they return "
            "ProjectedBalance / LoanState detail the seam composes, kept "
            "callable by the chart and loan-route consumers by design.",
        ),
    }

    def visit_call(self, node: nodes.Call) -> None:
        """Flag a balance-producer call made outside the seam + engine cluster.

        ``node`` is every call expression; only a call to one of the guarded
        balance producers from a module NOT in the seam allowlist is reported.
        The producer-name check (a frozenset lookup on the called name) runs
        first, so the module-identity walk runs only for an actual producer
        call.
        """
        producer = _called_balance_producer(node)
        if producer is None:
            return
        if _in_balance_seam_cluster(node):
            return
        self.add_message(
            "shekel-balance-producer-bypass", node=node, args=(producer,),
        )


def register(linter) -> None:
    """Register the Shekel checkers with the pylint ``linter`` (plugin entry point).

    Called by pylint when this module is named in ``.pylintrc``'s
    ``load-plugins``. ``linter`` is the active PyLinter instance.
    """
    linter.register_checker(ShekelMoneyChecker(linter))
    linter.register_checker(ShekelRefNameChecker(linter))
    linter.register_checker(ShekelDisableRationaleChecker(linter))
    linter.register_checker(ShekelLoanBalanceSourceChecker(linter))
    linter.register_checker(ShekelBalanceSeamChecker(linter))
