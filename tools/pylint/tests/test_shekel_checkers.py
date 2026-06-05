"""Unit tests for the Shekel project-specific pylint checkers.

Each checker is exercised against AST snippets built with ``astroid.extract_node``
and verified with pylint's ``CheckerTestCase`` harness. Every positive case
(the antipattern is flagged) is paired with the corresponding negative case (the
legitimate form is NOT flagged), because a checker that over-fires creates the
cargo-cult-disable noise the rules exist to prevent.
"""

import astroid
from pylint.testutils import CheckerTestCase, MessageTest

from shekel_checkers import ShekelMoneyChecker, ShekelRefNameChecker


class TestShekelMoneyChecker(CheckerTestCase):
    """``shekel-decimal-from-float``: Decimal must be built from strings, not floats."""

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
