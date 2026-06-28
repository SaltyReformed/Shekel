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
    ShekelBalanceSeamChecker,
    ShekelDisableRationaleChecker,
    ShekelLoanBalanceSourceChecker,
    ShekelMoneyChecker,
    ShekelRefNameChecker,
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
    growth_engine, net_worth_kernel) may call a balance producer directly. The
    rule keys off the ENCLOSING module (``node.root().name``), so each case is
    parsed inside a named module via :func:`astroid.parse` (``module_name=``)
    rather than the bare :func:`astroid.extract_node` the shape-only checkers
    use -- that yields an empty module name. Every flagged form is paired with
    the conforming form that must NOT fire, and two register-bound loops assert
    the fence covers EVERY guarded producer and EVERY allowlisted module.
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
        because the prose comment in ``shekel_checkers.py`` names it.
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
