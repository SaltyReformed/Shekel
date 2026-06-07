"""Unit tests for the Shekel project-specific pylint checkers.

Each checker is exercised against AST snippets built with ``astroid.extract_node``
and verified with pylint's ``CheckerTestCase`` harness. Every positive case
(the antipattern is flagged) is paired with the corresponding negative case (the
legitimate form is NOT flagged), because a checker that over-fires creates the
cargo-cult-disable noise the rules exist to prevent.
"""

import astroid
from pylint.testutils import CheckerTestCase, MessageTest

from shekel_checkers import (
    ShekelDisableRationaleChecker,
    ShekelMoneyChecker,
    ShekelRefNameChecker,
)


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
