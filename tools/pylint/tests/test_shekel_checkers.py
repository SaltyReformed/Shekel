"""Unit tests for the Shekel project-specific pylint checkers.

Each checker is exercised against AST snippets built with ``astroid.extract_node``
and verified with pylint's ``CheckerTestCase`` harness. Every positive case
(the antipattern is flagged) is paired with the corresponding negative case (the
legitimate form is NOT flagged), because a checker that over-fires creates the
cargo-cult-disable noise the rules exist to prevent.
"""

import astroid
from astroid import nodes
from pylint.testutils import CheckerTestCase, MessageTest

from shekel_checkers import (
    _BALANCE_PRODUCERS,
    _BALANCE_SEAM_MODULES,
    _LOAN_LEDGER_READER_MODULES,
    _LOAN_LEDGER_READER_PRODUCERS,
    _STATUS_SEAM_MODULES,
    ShekelBalanceSeamChecker,
    ShekelDisableRationaleChecker,
    ShekelLoanBalanceSourceChecker,
    ShekelMoneyChecker,
    ShekelRefNameChecker,
    ShekelTransactionStatusBypassChecker,
)


class TestShekelMoneyChecker(CheckerTestCase):
    """The ``shekel-money`` checker: two monetary call rules.

    ``shekel-decimal-from-float`` -- Decimal must be built from strings, not
    floats; ``shekel-bare-money-quantize`` -- money must be rounded through
    ``round_money`` (explicit ROUND_HALF_UP), never a bare ``.quantize()`` that
    falls back to banker's rounding.
    """

    CHECKER_CLASS = ShekelMoneyChecker

    def test_flags_float_literal(self) -> None:
        """Decimal(0.1) loses precision and must be flagged."""
        node = astroid.extract_node("Decimal(0.1)")
        with self.assertAddsMessages(
            MessageTest("shekel-decimal-from-float", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_signed_float_literal(self) -> None:
        """Decimal(-0.1) hides the float behind a unary minus; still flagged."""
        node = astroid.extract_node("Decimal(-0.1)")
        with self.assertAddsMessages(
            MessageTest("shekel-decimal-from-float", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_float_builtin_argument(self) -> None:
        """Decimal(float(x)) launders a float through float(); still flagged."""
        node = astroid.extract_node("Decimal(float(x))")
        with self.assertAddsMessages(
            MessageTest("shekel-decimal-from-float", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_qualified_decimal_from_float(self) -> None:
        """decimal.Decimal(0.1) (attribute form) is flagged just like the bare call."""
        node = astroid.extract_node("decimal.Decimal(0.1)")
        with self.assertAddsMessages(
            MessageTest("shekel-decimal-from-float", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_string_literal(self) -> None:
        """Decimal(\"0.1\") is exact and must NOT be flagged."""
        node = astroid.extract_node('Decimal("0.1")')
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_integer_literal(self) -> None:
        """Decimal(5) is exact; integer arguments are intentionally allowed."""
        node = astroid.extract_node("Decimal(5)")
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_variable_argument(self) -> None:
        """Decimal(x) cannot be statically proven float; not flagged (no false positive)."""
        node = astroid.extract_node("Decimal(x)")
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_ignores_non_decimal_call(self) -> None:
        """A float literal passed to some other callable is not this checker's concern."""
        node = astroid.extract_node("SomeWidget(0.1)")
        with self.assertNoMessages():
            self.checker.visit_call(node)

    # ── shekel-bare-money-quantize (W9904) ──────────────────────────

    def test_flags_bare_quantize_decimal_literal(self) -> None:
        """amount.quantize(Decimal(\"0.01\")) rounds money with banker's default; flagged."""
        node = astroid.extract_node('amount.quantize(Decimal("0.01"))')
        with self.assertAddsMessages(
            MessageTest("shekel-bare-money-quantize", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_bare_quantize_two_places_constant(self) -> None:
        """amount.quantize(TWO_PLACES) -- the named cents constant -- is flagged."""
        node = astroid.extract_node("amount.quantize(TWO_PLACES)")
        with self.assertAddsMessages(
            MessageTest("shekel-bare-money-quantize", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_bare_quantize_underscore_cents_constant(self) -> None:
        """amount.quantize(_TWO_PLACES) -- the private redeclaration form -- is flagged."""
        node = astroid.extract_node("total.quantize(_TWO_PLACES)")
        with self.assertAddsMessages(
            MessageTest("shekel-bare-money-quantize", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_quantize_with_rounding_keyword(self) -> None:
        """quantize(CENTS, rounding=ROUND_HALF_UP) selects the mode explicitly; not flagged."""
        node = astroid.extract_node(
            "amount.quantize(CENTS, rounding=ROUND_HALF_UP)",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_quantize_with_positional_rounding(self) -> None:
        """quantize(TWO_PLACES, ROUND_HALF_UP) -- positional mode -- is not flagged."""
        node = astroid.extract_node("amount.quantize(TWO_PLACES, ROUND_HALF_UP)")
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_bare_quantize_non_cents_quantum(self) -> None:
        """A bare quantize of a one-decimal percentage is not money; not flagged."""
        node = astroid.extract_node('pct.quantize(Decimal("0.1"))')
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_bare_quantize_named_percentage_quantum(self) -> None:
        """A bare quantize of a non-cents named quantum (_PCT_QUANTUM) is not flagged."""
        node = astroid.extract_node("rate.quantize(_PCT_QUANTUM)")
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_round_money_helper_call(self) -> None:
        """round_money(x) is the sanctioned boundary helper, not a bare quantize; not flagged."""
        node = astroid.extract_node("round_money(amount)")
        with self.assertNoMessages():
            self.checker.visit_call(node)


class TestShekelRefNameChecker(CheckerTestCase):
    """``shekel-refname-compare``: logic must not compare .name to string literals."""

    CHECKER_CLASS = ShekelRefNameChecker

    def test_flags_name_equals_literal(self) -> None:
        """status.name == \"Projected\" is the reference-table antipattern."""
        node = astroid.extract_node('status.name == "Projected"')
        with self.assertAddsMessages(
            MessageTest("shekel-refname-compare", node=node),
            ignore_position=True,
        ):
            self.checker.visit_compare(node)

    def test_flags_reversed_operands(self) -> None:
        """\"Projected\" == status.name is the same smell with operands swapped."""
        node = astroid.extract_node('"Projected" == status.name')
        with self.assertAddsMessages(
            MessageTest("shekel-refname-compare", node=node),
            ignore_position=True,
        ):
            self.checker.visit_compare(node)

    def test_flags_nested_attribute(self) -> None:
        """txn.status.name == \"Projected\" still reads the display column."""
        node = astroid.extract_node('txn.status.name == "Projected"')
        with self.assertAddsMessages(
            MessageTest("shekel-refname-compare", node=node),
            ignore_position=True,
        ):
            self.checker.visit_compare(node)

    def test_flags_inequality(self) -> None:
        """status.name != \"Projected\" is flagged like equality."""
        node = astroid.extract_node('status.name != "Projected"')
        with self.assertAddsMessages(
            MessageTest("shekel-refname-compare", node=node),
            ignore_position=True,
        ):
            self.checker.visit_compare(node)

    def test_flags_membership_in_literal_tuple(self) -> None:
        """status.name in (\"done\", \"credit\") keys logic off display labels."""
        node = astroid.extract_node('status.name in ("done", "credit")')
        with self.assertAddsMessages(
            MessageTest("shekel-refname-compare", node=node),
            ignore_position=True,
        ):
            self.checker.visit_compare(node)

    def test_allows_name_equals_subscript(self) -> None:
        """AccountType.name == data[\"name\"] compares to user input, not a literal."""
        node = astroid.extract_node('AccountType.name == data["name"]')
        with self.assertNoMessages():
            self.checker.visit_compare(node)

    def test_allows_name_equals_variable(self) -> None:
        """status.name == expected (a variable) is a legitimate dynamic comparison."""
        node = astroid.extract_node("status.name == expected")
        with self.assertNoMessages():
            self.checker.visit_compare(node)

    def test_allows_id_comparison(self) -> None:
        """status_id == 3 keys off the ID column and is the correct pattern."""
        node = astroid.extract_node("status_id == 3")
        with self.assertNoMessages():
            self.checker.visit_compare(node)

    def test_allows_non_name_attribute(self) -> None:
        """request.method == \"POST\" reads .method, not the .name display column."""
        node = astroid.extract_node('request.method == "POST"')
        with self.assertNoMessages():
            self.checker.visit_compare(node)


class TestShekelDisableRationaleChecker(CheckerTestCase):
    """``shekel-disable-rationale``: every disable needs a standard ``Pylint:`` note.

    Exercised through ``process_module`` against whole-module sources parsed with
    ``astroid.parse`` (whose ``stream()`` yields the source the raw checker
    tokenizes). Each ``def``/``class``-scoped case (rationale in the docstring) is
    paired with a statement-scoped case (rationale in a comment immediately above),
    and every positive (flagged) case is paired with the conforming form that must
    NOT fire -- a checker that over-fires would itself become disable noise.
    """

    CHECKER_CLASS = ShekelDisableRationaleChecker

    def test_allows_def_with_docstring_rationale(self) -> None:
        """A def-line disable justified in the docstring naming every rule passes."""
        module = astroid.parse(
            'def f(a, b, c, d, e, f):  '
            '# pylint: disable=too-many-arguments,too-many-positional-arguments\n'
            '    """Do a thing.\n'
            "\n"
            "    Pylint: ``too-many-arguments`` (6/5) / "
            "``too-many-positional-arguments`` (6/5) -- irreducible inputs.\n"
            '    """\n'
            "    return a\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_flags_def_without_marker(self) -> None:
        """A def-line disable whose docstring lacks the ``Pylint:`` marker is flagged."""
        module = astroid.parse(
            "def f(a, b, c, d, e, f):  # pylint: disable=too-many-arguments\n"
            '    """Do a thing with no rationale for the disable."""\n'
            "    return a\n"
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-disable-rationale",
                line=1,
                args=("too-many-arguments", "in the docstring"),
            ),
            ignore_position=True,
        ):
            self.checker.process_module(module)

    def test_flags_def_missing_one_rule_name(self) -> None:
        """A multi-rule disable must name EVERY rule in the docstring, not just one."""
        module = astroid.parse(
            "def f(a, b, c, d, e, f):  "
            "# pylint: disable=too-many-arguments,too-many-positional-arguments\n"
            '    """Do a thing.\n'
            "\n"
            "    Pylint: ``too-many-arguments`` (6/5) -- only one rule named.\n"
            '    """\n'
            "    return a\n"
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-disable-rationale",
                line=1,
                args=(
                    "too-many-arguments, too-many-positional-arguments",
                    "in the docstring",
                ),
            ),
            ignore_position=True,
        ):
            self.checker.process_module(module)

    def test_allows_class_with_docstring_rationale(self) -> None:
        """A class-line disable justified in the docstring passes."""
        module = astroid.parse(
            "class Bag:  # pylint: disable=too-many-instance-attributes\n"
            '    """A flat record.\n'
            "\n"
            "    Pylint: ``too-many-instance-attributes`` (8/7) -- flat aggregate.\n"
            '    """\n'
            "\n"
            "    x = 1\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_allows_decorated_def_disable(self) -> None:
        """The directive sits on the ``def`` line, not the decorator -- fromlineno maps it."""
        module = astroid.parse(
            "import functools\n"
            "@functools.cache\n"
            "def f():  # pylint: disable=too-many-return-statements\n"
            '    """Do a thing.\n'
            "\n"
            "    Pylint: ``too-many-return-statements`` (7/6) -- distinct exits.\n"
            '    """\n'
            "    return 1\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_allows_statement_with_comment_above(self) -> None:
        """A statement-scoped disable with a ``# Pylint:`` comment immediately above passes."""
        module = astroid.parse(
            "def h():\n"
            '    """Do a thing."""\n'
            "    # Pylint: ``invalid-name`` -- a single-letter loop alias reads clearer.\n"
            "    X = 1  # pylint: disable=invalid-name\n"
            "    return X\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_allows_standalone_disable_comment_with_rationale_above(self) -> None:
        """The deferred-import pattern: rationale above a standalone disable line."""
        module = astroid.parse(
            "def imp():\n"
            '    """Do a thing."""\n'
            "    # Pylint: ``import-outside-toplevel`` -- deferred to break a cycle.\n"
            "    # pylint: disable=import-outside-toplevel\n"
            "    import os\n"
            "    return os\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_flags_statement_without_comment_above(self) -> None:
        """A statement-scoped disable with no comment above is flagged."""
        module = astroid.parse(
            "def h():\n"
            '    """Do a thing."""\n'
            "    X = 1  # pylint: disable=invalid-name\n"
            "    return X\n"
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-disable-rationale",
                line=3,
                args=("invalid-name", "in a comment immediately above"),
            ),
            ignore_position=True,
        ):
            self.checker.process_module(module)

    def test_flags_statement_comment_separated_by_blank_line(self) -> None:
        """A rationale separated from the directive by a blank line does not count."""
        module = astroid.parse(
            "def h():\n"
            '    """Do a thing."""\n'
            "    # Pylint: ``invalid-name`` -- reason that floats away.\n"
            "\n"
            "    X = 1  # pylint: disable=invalid-name\n"
            "    return X\n"
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-disable-rationale",
                line=5,
                args=("invalid-name", "in a comment immediately above"),
            ),
            ignore_position=True,
        ):
            self.checker.process_module(module)

    def test_ignores_disable_text_inside_string_literal(self) -> None:
        """``# pylint: disable=`` inside a string is not a directive (no false positive)."""
        module = astroid.parse('S = "# pylint: disable=too-many-arguments"\n')
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_ignores_enable_directive(self) -> None:
        """``# pylint: enable=`` is not a suppression and needs no rationale."""
        module = astroid.parse("X = 1  # pylint: enable=too-many-arguments\n")
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_flags_combined_noqa_disable_without_rationale(self) -> None:
        """A ``# noqa: ...  pylint: disable=`` combined comment cannot evade the gate.

        Pylint honors the directive anywhere in the comment, so the checker
        must too: the historical combined trailing form used to slip past the
        old ``#\\s*pylint:`` regex entirely, leaving an undocumented
        suppression invisible to the rationale audit.
        """
        module = astroid.parse(
            "import sys\n"
            "sys.path.insert(0, '.')\n"
            "import os  # noqa: E402  pylint: disable=wrong-import-position\n"
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-disable-rationale",
                line=3,
                args=("wrong-import-position", "in a comment immediately above"),
            ),
            ignore_position=True,
        ):
            self.checker.process_module(module)

    def test_allows_combined_noqa_disable_with_rationale_above(self) -> None:
        """The combined form passes once the standard rationale sits above it."""
        module = astroid.parse(
            "import sys\n"
            "sys.path.insert(0, '.')\n"
            "# Pylint: ``wrong-import-position`` -- the bootstrap must precede it.\n"
            "import os  # noqa: E402  pylint: disable=wrong-import-position\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)


class TestShekelLoanBalanceSourceChecker(CheckerTestCase):
    """The loan balance-map fallback must be the resolver balance, not a stored column.

    ``compute_loan_period_balance_map`` / ``balance_from_schedule_at_date`` take
    the loan's resolver-derived ``current_balance`` as the pre-first-payment /
    empty-schedule fallback; passing a stored column (``original_principal`` /
    ``current_principal``) is the recurring net-worth bug (F-21 / PR #44). Every
    flagged form is paired with the conforming call that must NOT fire.
    """

    CHECKER_CLASS = ShekelLoanBalanceSourceChecker

    def test_flags_original_principal_attribute(self) -> None:
        """compute_loan_period_balance_map(..., params.original_principal): the PR #44 bug."""
        node = astroid.extract_node(
            "compute_loan_period_balance_map(schedule, periods, params.original_principal)",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-original-principal-as-balance", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_original_principal_on_other_producer(self) -> None:
        """balance_from_schedule_at_date(..., params.original_principal) is flagged too."""
        node = astroid.extract_node(
            "balance_from_schedule_at_date(sorted_schedule, target, params.original_principal)",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-original-principal-as-balance", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_bare_name_original_principal(self) -> None:
        """The bare-name parameter form (the live /savings bug pre-fix) is flagged."""
        node = astroid.extract_node(
            "compute_loan_period_balance_map(schedule, periods, original_principal)",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-original-principal-as-balance", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_current_principal_keyword(self) -> None:
        """The demoted current_principal column passed by the current_balance keyword is flagged."""
        node = astroid.extract_node(
            "compute_loan_period_balance_map(schedule, periods, "
            "current_balance=acct.current_principal)",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-original-principal-as-balance", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_qualified_producer_call(self) -> None:
        """The attribute-call form (module.compute_loan_period_balance_map) is flagged."""
        node = astroid.extract_node(
            "account_projection.compute_loan_period_balance_map("
            "schedule, periods, params.original_principal)",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-original-principal-as-balance", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_current_balance_attribute(self) -> None:
        """The resolver-derived state.current_balance is the correct fallback; not flagged."""
        node = astroid.extract_node(
            "compute_loan_period_balance_map(schedule, periods, state.current_balance)",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_bare_current_balance_name(self) -> None:
        """A bare current_balance local (the year-end form) is the correct fallback; not flagged."""
        node = astroid.extract_node(
            "balance_from_schedule_at_date(sorted_schedule, target, current_balance)",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_ignores_original_principal_to_other_function(self) -> None:
        """original_principal passed to an unrelated function is not this checker's concern."""
        node = astroid.extract_node(
            "build_rate_periods(terms, params.original_principal)",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_ignores_call_without_balance_argument(self) -> None:
        """A producer call missing the balance argument is not flagged and does not crash."""
        node = astroid.extract_node(
            "compute_loan_period_balance_map(schedule, periods)",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)


class TestShekelBalanceSeamChecker(CheckerTestCase):
    """``shekel-balance-producer-bypass``: balances come through the seam only.

    Every screen must obtain an account's balance through
    ``app.services.balance_at``; only the seam and the engine cluster it
    composes (balance_resolver, balance_calculator, account_projection,
    net_worth_kernel) may call a balance producer directly. The
    rule keys off the ENCLOSING module (``node.root().name``), so each case is
    parsed inside a named module via :func:`astroid.parse` (``module_name=``)
    rather than the bare :func:`astroid.extract_node` the shape-only checkers
    use -- that yields an empty module name. Every flagged form is paired with
    the conforming form that must NOT fire, and register-bound loops assert
    the fence covers EVERY guarded producer and EVERY allowlisted module --
    at the call site AND at the import (the aliased-import evasion class the
    2026-07-02 review's R3 closed).
    """

    CHECKER_CLASS = ShekelBalanceSeamChecker

    @staticmethod
    def _producer_call(call_source: str, module_name: str) -> nodes.Call:
        """Return the Call node for *call_source* parsed inside *module_name*.

        The enclosing module's name drives the seam allowlist check, so it is
        set explicitly. The snippet is a single assignment, so the module
        body's one statement carries the call under test as its value -- no
        nested calls, so the node is unambiguous.
        """
        module = astroid.parse(
            f"result = {call_source}\n", module_name=module_name,
        )
        return module.body[0].value

    def test_flags_attribute_producer_from_consumer(self) -> None:
        """A route calling balance_resolver.balances_for directly is flagged."""
        node = self._producer_call(
            "balance_resolver.balances_for(account, scenario_id, periods)",
            "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("balances_for",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_bare_name_producer_from_consumer(self) -> None:
        """A bare-imported producer call from a consumer is flagged.

        Uses compute_loan_period_balance_map -- imported and called by its bare
        name, the form net_worth_kernel itself uses internally.
        """
        node = self._producer_call(
            "compute_loan_period_balance_map(schedule, periods, current_balance)",
            "app.services.savings_dashboard_service._projections",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("compute_loan_period_balance_map",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_private_investment_builder_from_consumer(self) -> None:
        """The private _build_investment_balance_map is guarded too: no reaching past the seam."""
        node = self._producer_call(
            "net_worth_kernel._build_investment_balance_map("
            "account, params, scenario, periods)",
            "app.services.investment_dashboard_service",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("_build_investment_balance_map",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_every_guarded_producer_from_a_consumer(self) -> None:
        """EVERY name in _BALANCE_PRODUCERS is flagged when called from a consumer.

        Binds the test to the producer set itself, so a name added to (or
        dropped from) the frozenset is automatically covered -- the fence is
        only as strong as that set is complete.
        """
        for producer in sorted(_BALANCE_PRODUCERS):
            node = self._producer_call(
                f"{producer}(account, scenario, periods)", "app.routes.grid",
            )
            with self.assertAddsMessages(
                MessageTest(
                    "shekel-balance-producer-bypass",
                    node=node,
                    args=(producer,),
                ),
                ignore_position=True,
            ):
                self.checker.visit_call(node)

    def test_allows_producer_from_seam(self) -> None:
        """The seam itself (app.services.balance_at) may call a producer; not flagged."""
        node = self._producer_call(
            "balance_resolver.balances_for(account, scenario_id, periods)",
            "app.services.balance_at",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_producer_from_every_engine_cluster_module(self) -> None:
        """Each allowlisted engine-cluster module may call a producer (they compose each other).

        The companion to the every-producer loop: asserts the allowlist covers
        every module the seam's documented dependency direction names, so
        narrowing the set would surface here rather than as a surprise W9906 on
        an engine module. The allowlist holds fully-qualified names, so each is
        used directly as the enclosing module.
        """
        for module_name in sorted(_BALANCE_SEAM_MODULES):
            node = self._producer_call(
                "compute_loan_period_balance_map(schedule, periods, current_balance)",
                module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_call(node)

    def test_allows_producer_from_cluster_package_submodule(self) -> None:
        """A submodule of a cluster module (if one is split into a package) stays inside the fence.

        Locks the package-prefix match in :func:`_in_balance_seam_cluster`: a
        future ``app/services/balance_resolver/_core.py`` resolves to
        ``app.services.balance_resolver._core`` and must remain exempt.
        """
        node = self._producer_call(
            "balances_for(account, scenario_id, periods)",
            "app.services.balance_resolver._core",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_flags_same_basename_in_another_package(self) -> None:
        """A same-named module in another package is NOT exempted (no silent bypass by collision).

        The fence keys off the FULL module path, so a hypothetical
        ``app/routes/balance_at.py`` -- basename ``balance_at`` -- is still
        flagged for a direct producer call. This is the false-negative the
        basename-only match would have allowed.
        """
        node = self._producer_call(
            "balance_resolver.balances_for(account, scenario_id, periods)",
            "app.routes.balance_at",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("balances_for",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_producer_in_unresolvable_module(self) -> None:
        """An empty / unresolvable module name fails closed: the producer call is flagged.

        Locks the documented fail-closed behavior of
        :func:`_in_balance_seam_cluster` -- when the module name cannot be
        resolved, the safe direction for a fence is to flag, not exempt.
        """
        node = self._producer_call(
            "balances_for(account, scenario_id, periods)", "",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("balances_for",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_every_loan_ledger_reader_from_a_consumer(self) -> None:
        """EVERY genesis loan-ledger reader is flagged from a consumer module.

        The read switch's final commit fences the confirmed balance readers:
        a route or dashboard calling one directly bypasses the
        ``confirmed_loan_view`` injection seam.  Bound to the reader set
        itself so a future reader added to the frozenset is automatically
        covered.
        """
        for reader in sorted(_LOAN_LEDGER_READER_PRODUCERS):
            node = self._producer_call(
                f"{reader}(account_id, scenario_id, as_of)",
                "app.routes.loan.dashboard",
            )
            with self.assertAddsMessages(
                MessageTest(
                    "shekel-balance-producer-bypass",
                    node=node,
                    args=(reader,),
                ),
                ignore_position=True,
            ):
                self.checker.visit_call(node)

    def test_allows_loan_ledger_reader_from_every_sanctioned_module(self) -> None:
        """Each reader-allowlisted module may call a genesis reader; not flagged.

        The reader allowlist is the seam cluster PLUS the defining
        ``loan_posting_service`` package and the ``loan_payment_service``
        view seam; each is asserted, so narrowing the set surfaces here
        rather than as a surprise W9906 on the injection path.
        """
        for module_name in sorted(_LOAN_LEDGER_READER_MODULES):
            node = self._producer_call(
                "confirmed_loan_balance_at(account_id, scenario_id, as_of)",
                module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_call(node)

    def test_allows_loan_ledger_reader_from_posting_package_submodule(self) -> None:
        """The defining package's submodules stay inside the reader fence.

        ``loan_posting_service`` is a package; its ``_reader`` / oracle-facing
        internals resolve to submodule names and must remain exempt via the
        package-prefix match.
        """
        node = self._producer_call(
            "confirmed_loan_balance_at(account_id, scenario_id, as_of)",
            "app.services.loan_posting_service._reader",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_loan_ledger_reader_fails_closed_in_unresolvable_module(self) -> None:
        """An unresolvable module name fails closed for the readers too."""
        node = self._producer_call(
            "confirmed_loan_balance_map(account_id, scenario_id, periods)", "",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("confirmed_loan_balance_map",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    # ── the import-level fence (the aliased-import evasion class, R3) ──

    @staticmethod
    def _import_node(import_source: str, module_name: str) -> nodes.ImportFrom:
        """Return the ImportFrom node for *import_source* parsed in *module_name*.

        The enclosing module's name drives the allowlist check, so it is set
        explicitly; the snippet is a single import statement, so the module
        body's one statement is the ``ImportFrom`` under test.
        """
        module = astroid.parse(f"{import_source}\n", module_name=module_name)
        return module.body[0]

    def test_flags_aliased_producer_import_from_consumer(self) -> None:
        """``from ... import balances_for as bf`` from a consumer is flagged.

        This is the evasion class the import fence exists for: after the
        aliased import every call reads ``bf(...)``, which matches no producer
        name, so call-site matching alone would never fire again.
        """
        node = self._import_node(
            "from app.services.balance_resolver import balances_for as bf",
            "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("balances_for",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_every_producer_import_from_consumer(self) -> None:
        """EVERY name in _BALANCE_PRODUCERS is flagged when imported by a consumer.

        The unaliased form is flagged too -- a module that may not call a
        producer has no legitimate reason to import it.  Bound to the producer
        set itself, like the call-site loop, so the import fence can never
        silently cover fewer names than the call fence.
        """
        for producer in sorted(_BALANCE_PRODUCERS):
            node = self._import_node(
                f"from app.services.engine import {producer}",
                "app.routes.grid",
            )
            with self.assertAddsMessages(
                MessageTest(
                    "shekel-balance-producer-bypass",
                    node=node,
                    args=(producer,),
                ),
                ignore_position=True,
            ):
                self.checker.visit_importfrom(node)

    def test_flags_multi_name_producer_import_reports_each(self) -> None:
        """One import statement naming two producers reports both."""
        node = self._import_node(
            "from app.services.balance_calculator import "
            "calculate_balances, calculate_balances_with_interest",
            "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("calculate_balances",),
            ),
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("calculate_balances_with_interest",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_allows_producer_import_from_every_cluster_module(self) -> None:
        """Each engine-cluster module may import a producer (they compose each other)."""
        for module_name in sorted(_BALANCE_SEAM_MODULES):
            node = self._import_node(
                "from app.services.account_projection import "
                "compute_loan_period_balance_map",
                module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_importfrom(node)

    def test_allows_module_import_from_consumer(self) -> None:
        """``from app.services import balance_calculator`` imports the MODULE; not flagged.

        A module import keeps the producer's own name at every call site
        (``balance_calculator.calculate_balances(...)``), where the call fence
        already sees it -- so it needs no import-level guard.
        """
        node = self._import_node(
            "from app.services import balance_calculator",
            "app.routes.grid",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_flags_loan_ledger_reader_import_from_consumer(self) -> None:
        """A genesis loan-ledger reader imported by a consumer is flagged (aliased or not)."""
        node = self._import_node(
            "from app.services.loan_posting_service import "
            "confirmed_loan_balance_at as reader",
            "app.routes.loan.dashboard",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("confirmed_loan_balance_at",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_allows_loan_ledger_reader_import_from_every_sanctioned_module(self) -> None:
        """Each reader-allowlisted module may import a genesis reader.

        Covers the two real in-tree import shapes: the defining package's
        ``__init__`` re-export and net_worth_kernel's documented private
        ``_reader`` reach-in (both resolve through the reader allowlist).
        """
        for module_name in sorted(_LOAN_LEDGER_READER_MODULES):
            node = self._import_node(
                "from app.services.loan_posting_service._reader import "
                "confirmed_loan_balance_map",
                module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_importfrom(node)

    def test_allows_seam_entry_call_from_consumer(self) -> None:
        """A consumer calling the seam's own balance_map entry is the sanctioned path; not flagged.

        ``balance_map`` is a seam entry point, not a guarded producer, so the
        attribute name does not match -- this is exactly how every rerouted
        consumer now reads balances.
        """
        node = self._producer_call(
            "balance_at.balance_map(account, scenario, periods)",
            "app.routes.grid",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_project_balance_from_consumer(self) -> None:
        """project_balance is a rich primitive, not a producer; not flagged.

        It returns ProjectedBalance contribution/growth detail the seam
        composes, so a chart consumer may call it directly.
        """
        node = self._producer_call(
            "growth_engine.project_balance(account, params, scenario, periods)",
            "app.services.investment_dashboard_service",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_resolve_loan_from_consumer(self) -> None:
        """resolve_loan returns the rich LoanState, not a balance map; never flagged."""
        node = self._producer_call(
            "loan_resolver.resolve_loan(account, scenario_id)",
            "app.routes.loan._helpers",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_flags_investment_base_balance_map_from_consumer(self) -> None:
        """The cash-basis seed accessor IS guarded: a consumer call is flagged.

        Closing the fence hole made ``net_worth_kernel.investment_base_balance_map``
        a guarded producer.  It returns a DISPLAY-shaped cash-basis (pre-growth)
        map -- the one balance-map accessor a consumer could have rendered as a
        real balance (the investment understatement bug the seam exists to
        kill).  A consumer reaching it directly is now flagged; the sanctioned
        seed read is the seam entry (see the next test).  This is also covered
        by ``test_flags_every_guarded_producer_from_a_consumer``; kept explicit
        because the prose comment in ``shekel_checkers/balance_seam.py`` names it.
        """
        node = self._producer_call(
            "net_worth_kernel.investment_base_balance_map(account, scenario, periods)",
            "app.services.investment_dashboard_service",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-balance-producer-bypass",
                node=node,
                args=("investment_base_balance_map",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_investment_seed_map_seam_entry_from_consumer(self) -> None:
        """The seam's investment_seed_map is the compliant seed read; never flagged.

        After the fence hole closed, the sanctioned consumers (investment /
        retirement / year-end growth) read the cash-basis seed through
        ``balance_at.investment_seed_map`` instead of the now-guarded kernel
        producer.  That seam entry is NOT a producer name, so a consumer calling
        it is never flagged -- the fence-compliant path the reroute put every
        seed consumer on.
        """
        node = self._producer_call(
            "balance_at.investment_seed_map(account, scenario, periods)",
            "app.services.investment_dashboard_service",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_ignores_unrelated_call_from_consumer(self) -> None:
        """A call to some unrelated function is not this checker's concern."""
        node = self._producer_call(
            "build_rate_periods(terms, principal)", "app.routes.grid",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)


class TestShekelTransactionStatusBypassChecker(CheckerTestCase):
    """``shekel-transaction-status-bypass``: status_id written only via the seam.

    Every non-transfer ``Transaction.status_id`` change must route through
    ``status_seam.apply_status_change``; only that module
    (``app.services.status_seam``) and ``transfer_service`` (which mirrors status
    onto a transfer's two shadow rows) may write it.  Four write forms are
    fenced (H3/R3 of the 2026-07-02 review closed the last three): direct
    assignment, the literal ``setattr`` form, a ``status_id`` payload in a bulk
    ``.update()`` / ``.values()`` call, and a born-settled ``Transaction`` /
    ``Transfer`` constructor kwarg.  Like the W9906 fence the
    rule keys off the ENCLOSING module (``node.root().name``), so each snippet is
    parsed inside a named module via :func:`astroid.parse` (``module_name=``).
    Every flagged form is paired with the conforming form that must NOT fire, and
    register-bound loops assert every allowlisted module is exempt from every
    form.
    """

    CHECKER_CLASS = ShekelTransactionStatusBypassChecker

    @staticmethod
    def _status_assign(assign_source: str, module_name: str) -> nodes.AssignAttr:
        """Return the AssignAttr target of *assign_source* parsed in *module_name*.

        The enclosing module's name drives the allowlist check, so it is set
        explicitly.  The snippet is a single attribute assignment, so the module
        body's one statement is an ``Assign`` whose first target is the
        store-context ``AssignAttr`` the checker visits.
        """
        module = astroid.parse(f"{assign_source}\n", module_name=module_name)
        return module.body[0].targets[0]

    def test_flags_status_id_assignment_from_route(self) -> None:
        """A bare status_id assignment in a route module is flagged."""
        node = self._status_assign(
            "txn.status_id = new_status_id",
            "app.routes.transactions.mutations",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_flags_status_id_assignment_from_credit_workflow(self) -> None:
        """A status_id assignment in credit_workflow is flagged (must use the seam)."""
        node = self._status_assign(
            "txn.status_id = credit_id", "app.services.credit_workflow",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_allows_status_id_assignment_in_status_seam(self) -> None:
        """The seam's own module (status_seam) may assign status_id; not flagged."""
        node = self._status_assign(
            "txn.status_id = new_status_id", "app.services.status_seam",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_allows_status_id_assignment_in_transfer_service(self) -> None:
        """transfer_service mirrors status onto its two shadow rows; not flagged.

        A name-based checker cannot distinguish a shadow ``Transaction``'s
        ``status_id`` from a real transaction's, so transfer_service is
        allowlisted alongside the seam (2.8b MEDIUM).
        """
        node = self._status_assign(
            "expense_shadow.status_id = new_status_id",
            "app.services.transfer_service",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_allows_every_seam_module(self) -> None:
        """EVERY allowlisted module may assign status_id directly.

        Binds the test to :data:`_STATUS_SEAM_MODULES` itself, so narrowing the
        set would surface here rather than as a surprise W9907 on a seam module.
        """
        for module_name in sorted(_STATUS_SEAM_MODULES):
            node = self._status_assign(
                "txn.status_id = new_status_id", module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_assignattr(node)

    def test_allows_seam_package_submodule(self) -> None:
        """A submodule of a seam module (if it is split into a package) stays exempt.

        Locks the package-prefix match: a future
        ``app/services/status_seam/_core.py`` resolves to
        ``app.services.status_seam._core`` and must remain exempt.
        """
        node = self._status_assign(
            "txn.status_id = new_status_id",
            "app.services.status_seam._core",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_flags_same_basename_in_another_package(self) -> None:
        """A same-named module in another package is NOT exempted (no collision bypass).

        The allowlist keys off the FULL module path, so a hypothetical
        ``app/routes/status_seam.py`` is still flagged.
        """
        node = self._status_assign(
            "txn.status_id = x", "app.routes.status_seam",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_ignores_other_attribute_assignment(self) -> None:
        """Assigning a non-status_id attribute is not this checker's concern."""
        node = self._status_assign(
            "txn.estimated_amount = amount",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_ignores_filing_status_id_assignment(self) -> None:
        """filing_status_id (a different attribute on the tax tables) is not matched."""
        node = self._status_assign(
            "profile.filing_status_id = fs_id",
            "app.services.tax_config_service",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_ignores_status_id_read_in_assigned_value(self) -> None:
        """A status_id READ on the value side is never flagged; only the target counts.

        ``txn.notes = (other.status_id == 5)`` assigns ``notes`` (the
        ``AssignAttr`` the checker visits); the ``other.status_id`` on the value
        side is a load-context ``Attribute`` the visitor never receives, so no
        message fires -- the structural reason a ``status_id ==`` comparison is
        immune.
        """
        node = self._status_assign(
            "txn.notes = (other.status_id == 5)",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    # ── the call-shaped write forms (H3/R3: ctor, setattr, bulk) ──────

    @staticmethod
    def _status_call(call_source: str, module_name: str) -> nodes.Call:
        """Return the Call node of *call_source* parsed in *module_name*.

        Mirrors the seam checker's ``_producer_call``: the snippet is a single
        statement (a plain expression or an assignment), so the module body's
        one statement carries the OUTER call under test as its ``value`` --
        exactly the node a real run dispatches to ``visit_call`` for the
        method-call forms (``.update`` / ``.values``), whose inner calls are
        separate nodes.
        """
        module = astroid.parse(f"{call_source}\n", module_name=module_name)
        return module.body[0].value

    def test_flags_born_settled_ctor(self) -> None:
        """Transaction(status_id=done_id) constructs a born-settled row; flagged.

        A born-settled row would carry NULL paid_at, skip verify_transition,
        and emit no ledger posting -- the H3 failure mode.
        """
        node = self._status_call(
            "txn = Transaction(status_id=done_id, name=name)",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_ctor_with_settled_ref_cache_lookup(self) -> None:
        """A ref-cache lookup of a NON-Projected member in a ctor is flagged."""
        node = self._status_call(
            "txn = Transaction(status_id=ref_cache.status_id(StatusEnum.PAID))",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_ctor_with_opaque_status_variable(self) -> None:
        """An unrecognizable ctor status value fails closed and is flagged.

        ``Transaction(status_id=some_status)`` cannot be statically proven
        Projected; the fence's dangerous mode is the false negative, so the
        author must spell the value in a canonical Projected form (the
        ref-cache PROJECTED lookup or a projected_id name) or settle through
        the seam after creation.
        """
        node = self._status_call(
            "txn = Transaction(status_id=some_status)",
            "app.routes.transactions.create",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_transfer_ctor_born_settled(self) -> None:
        """Transfer(status_id=done_id) outside the seam modules is flagged too."""
        node = self._status_call(
            "xfer = Transfer(status_id=done_id)",
            "app.routes.transfers.mutations",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_attribute_form_model_ctor(self) -> None:
        """models.Transaction(status_id=done_id) -- the qualified ctor -- is flagged."""
        node = self._status_call(
            "txn = models.Transaction(status_id=done_id)",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_ctor_born_projected_ref_cache_lookup(self) -> None:
        """Transaction(status_id=ref_cache.status_id(StatusEnum.PROJECTED)) is the rule; not flagged.

        The credit-workflow / carry-forward create form.
        """
        node = self._status_call(
            "payback = Transaction("
            "status_id=ref_cache.status_id(StatusEnum.PROJECTED), name=name)",
            "app.services.credit_workflow",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_born_projected_plan_attribute(self) -> None:
        """Transaction(status_id=plan.projected_id) -- the recurrence-engine form -- is not flagged."""
        node = self._status_call(
            "txn = Transaction(status_id=plan.projected_id)",
            "app.services.recurrence_engine",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_born_projected_bare_name(self) -> None:
        """A bare projected_id local as the ctor status is recognized; not flagged.

        (A Transfer constructed outside transfer_service would violate the
        transfer INVARIANTS -- but that is a different rule with its own
        guards, not the status fence's concern.)
        """
        node = self._status_call(
            "xfer = Transfer(status_id=projected_id)",
            "app.routes.transfers.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_splat_kwargs(self) -> None:
        """Transaction(**data) carries no named status kwarg; not flagged.

        The create-route form: statically invisible, governed by the
        documented convention (the schema omits status_id and the route
        assigns Projected into the dict unconditionally).
        """
        node = self._status_call(
            "txn = Transaction(**data)",
            "app.routes.transactions.create",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_without_status_kwarg(self) -> None:
        """A model ctor with no status_id kwarg is not this fence's concern."""
        node = self._status_call(
            "txn = Transaction(name=name, estimated_amount=amount)",
            "app.services.some_import_service",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_transfer_spec_with_any_status(self) -> None:
        """TransferSpec(status_id=...) is a service spec, not a model ctor; not flagged.

        The spec is consumed by transfer_service.create_transfer, which owns
        the actual model construction inside the allowlist.
        """
        node = self._status_call(
            "spec = transfer_service.TransferSpec(status_id=done_id)",
            "app.routes.transfers.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_service_call_with_status_kwarg(self) -> None:
        """update_transfer(..., status_id=...) is the sanctioned service path; not flagged."""
        node = self._status_call(
            "transfer_service.update_transfer("
            "xfer_id, user_id, status_id=cancelled_id)",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_born_settled_ctor_in_transfer_service(self) -> None:
        """transfer_service constructs shadows with the parent's status; exempt.

        Shadow rows are born with spec.status_id / the parent transfer's
        status -- not necessarily Projected -- and transfer_service is the
        sanctioned owner of that construction.
        """
        node = self._status_call(
            "shadow = Transaction(status_id=spec.status_id)",
            "app.services.transfer_service",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_flags_setattr_status_literal(self) -> None:
        """setattr(txn, \"status_id\", value) writes the column past the AssignAttr visitor; flagged."""
        node = self._status_call(
            'setattr(txn, "status_id", value)',
            "app.routes.transactions.mutations",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_setattr_status_literal_in_seam(self) -> None:
        """The seam module may use any write form it owns; not flagged."""
        node = self._status_call(
            'setattr(txn, "status_id", new_status_id)',
            "app.services.status_seam",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_setattr_other_literal_field(self) -> None:
        """setattr of a different literal field is not this fence's concern."""
        node = self._status_call(
            'setattr(txn, "notes", value)',
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_setattr_dynamic_field_loop(self) -> None:
        """setattr(txn, field, value) -- the schema-loop form -- is not matched.

        Statically invisible: the mutations route's loop excludes status_id
        with a ``continue`` and routes it through the seam; that guard stays
        with review and the route tests, as documented on the checker.
        """
        node = self._status_call(
            "setattr(txn, field, value)",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_flags_query_update_with_status_string_key(self) -> None:
        """A bulk Query.update carrying a \"status_id\" key bypasses the seam; flagged."""
        node = self._status_call(
            "db.session.query(Transaction).update("
            '{"status_id": done_id}, synchronize_session="fetch")',
            "app.routes.templates",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_update_with_column_attribute_key(self) -> None:
        """The column-object key form ({Transaction.status_id: ...}) is flagged too."""
        node = self._status_call(
            "query.update({Transaction.status_id: done_id})",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_values_with_status_keyword(self) -> None:
        """update(Transaction).values(status_id=...) -- the Core form -- is flagged."""
        node = self._status_call(
            "update(Transaction).values(status_id=done_id)",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_update_with_dict_builder(self) -> None:
        """query.update(dict(status_id=...)) -- the dict() builder -- is flagged."""
        node = self._status_call(
            "query.update(dict(status_id=done_id))",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_update_without_status_key(self) -> None:
        """The in-tree bulk soft-delete form (is_deleted only) is not flagged."""
        node = self._status_call(
            'query.update({"is_deleted": True}, synchronize_session="fetch")',
            "app.routes.templates",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_update_with_filing_status_key(self) -> None:
        """filing_status_id (the tax tables) is a different key; not matched."""
        node = self._status_call(
            'query.update({"filing_status_id": fs_id})',
            "app.services.tax_config_service",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_dict_update_with_non_literal_payload(self) -> None:
        """context.update(extra) -- a payload passed by name -- is not statically visible.

        The in-tree plain-dict merge form; a bulk-write payload built away
        from the call is a documented residual, not a false positive.
        """
        node = self._status_call(
            "context.update(extra_context)",
            "app.routes.settings",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_every_seam_module_for_call_forms(self) -> None:
        """EVERY allowlisted module is exempt from the call-shaped forms too.

        The companion to test_allows_every_seam_module: binds the call-form
        exemption to :data:`_STATUS_SEAM_MODULES` itself, using the strictest
        form (a born-settled ctor).
        """
        for module_name in sorted(_STATUS_SEAM_MODULES):
            node = self._status_call(
                "txn = Transaction(status_id=done_id)", module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_call(node)
