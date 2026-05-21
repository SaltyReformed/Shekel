"""Static gates: no money arithmetic in Jinja or JS (MED-04 / E-16 / E-17).

Commit 31 of the financial calculation audit remediation moves every
money computation out of Jinja and JS into routes/services.  These
locks are the permanent guard against a regression that re-introduces
inline ``estimated_amount - entries.total`` (TA-NN) or client-side
``act - est`` (JN-NN); they live as integration tests so any new
template / JS file added later is checked automatically.

The gates mirror the verification commands the developer ran when
authoring the commit:

- ``grep -nE "\\{\\{[^}]*[-+*/][^}]*\\}\\}" app/templates/``
  must not flag arithmetic on money variables (loop counters and
  month / year non-money divisions are allowed).
- ``grep -nE "\\|float" app/templates/`` must be empty -- the
  Decimal-to-float cast at the display boundary is forbidden.
- ``grep -rnE "(act|est|amount|balance)\\s*[-+*/]\\s*(act|est|amount|balance)" app/static/js/``
  must be empty -- JS monetary values are display-only per coding
  standards.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path("app/templates")
JS_DIR = Path("app/static/js")

_FLOAT_RX = re.compile(r"\|float\b")
_JS_MONEY_ARITHMETIC_RX = re.compile(
    r"(act|est|amount|balance)\s*[-+*/]\s*(act|est|amount|balance)",
)

# Money-arithmetic anti-patterns inside Jinja ``{{ ... }}`` blocks.
# These are the specific shapes the audit catalogued as TA-NN
# arithmetic-in-template findings (E-16) plus the rate-times-100
# pattern the ``to_percent`` filter replaces.  Any new pattern that
# performs Decimal arithmetic on a monetary or rate variable in a
# template belongs here so a future regression is caught.
_TEMPLATE_MONEY_ANTIPATTERNS = (
    # ``estimated_amount - <something>`` / ``actual_amount - ...``,
    # the TA-01 / TA-02 grid-cell-remaining shape.
    re.compile(r"\{\{[^}]*(estimated_amount|actual_amount)\s*[-+*/]"),
    re.compile(r"\{\{[^}]*[-+*/]\s*(estimated_amount|actual_amount)"),
    # ``annual_amount / 12`` -- TA-04 escrow per-period shape.
    re.compile(r"\{\{[^}]*annual_amount\s*/"),
    # ``monthly_payment + ...`` / ``... + monthly_payment`` / etc.
    # TA-05 payoff combined-monthly shape.
    re.compile(r"\{\{[^}]*monthly_payment\s*[-+*/]"),
    # ``row.payment +``, ``row.principal +``, ``row.interest +``,
    # ``row.extra_payment +`` etc. -- TA-03 schedule-row sum.
    re.compile(r"\{\{[^}]*row\.\w+\s*[-+*/]"),
    # ``refi_principal - current_principal`` / sign flip -- TA-07/TA-08.
    re.compile(r"\{\{[^}]*(refi_principal|current_principal)\s*[-+*/]"),
    re.compile(r"\{\{[^}]*[-+*/]\s*(refi_principal|current_principal)"),
    # Unary negation on monetary variables -- TA-08 sign flip.
    re.compile(
        r"\{\{[^}]*-(refi_principal|current_principal|princ_diff|"
        r"monthly_savings|interest_savings)"
    ),
    # ``rate * 100`` patterns -- TA-10 / TA-11.  ``to_percent`` is the
    # sanctioned replacement; bare ``X * 100`` in a template is a
    # regression.
    re.compile(r"\{\{[^}]*\w*_rate\s*\*\s*100"),
    re.compile(r"\{\{[^}]*\*\s*100\b"),
    # ``net_pay / gross_biweekly`` -- the take-home rate division
    # that previously lived in salary/breakdown.html.
    re.compile(r"\{\{[^}]*net_pay\s*/"),
)


def _iter_files(root: Path, suffix: str) -> list[Path]:
    return sorted(p for p in root.rglob(f"*{suffix}") if p.is_file())


def test_c31_2_no_float_cast_in_any_template():
    """C31-2 -- ``|float`` is forbidden in every Jinja template.

    The ``|float`` filter was a binary-float cast on a Decimal at the
    display boundary, masking precision drift behind the formatter.
    Decimals format correctly via ``"{:,.2f}".format(value)`` without
    the cast, so the filter has no remaining legitimate use.
    """
    offenders: list[str] = []
    for path in _iter_files(TEMPLATES_DIR, ".html"):
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _FLOAT_RX.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, (
        "|float casts must not appear in templates (MED-04 / E-16). "
        "Use Decimal directly; format strings work on Decimal natively. "
        "Offending lines:\n" + "\n".join(offenders)
    )


def test_c31_1_no_money_arithmetic_in_jinja():
    """C31-1 -- the audited TA-NN money-arithmetic shapes do not appear.

    The audit catalogued the exact shapes (TA-01 through TA-08, plus
    the rate-times-100 TA-10 / TA-11 pattern, plus the
    ``net_pay / gross_biweekly`` ratio) as the money-arithmetic-in-
    template findings.  Each is encoded above; any new occurrence
    is a regression that this lock fails.

    The test is intentionally specific to monetary variable names
    rather than a blanket ``{{ ... [-+*/] ... }}`` regex because the
    blanket form has many legitimate non-money exceptions (loop
    counters, ternary string literals containing dashes, ISO date
    strings rendered as ``year - 1``), and listing those exceptions
    is less robust than naming the actual financial shapes.
    """
    offenders: list[str] = []
    for path in _iter_files(TEMPLATES_DIR, ".html"):
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for rx in _TEMPLATE_MONEY_ANTIPATTERNS:
                match = rx.search(line)
                if match is not None:
                    offenders.append(
                        f"{path}:{lineno}: {line.strip()}"
                    )
                    break
    assert not offenders, (
        "Money arithmetic is forbidden inside Jinja ``{{ ... }}`` "
        "(MED-04 / E-16). Move the computation to the owning route "
        "or service and pass a ready-to-render Decimal value. "
        "Offending lines:\n" + "\n".join(offenders)
    )


def test_c31_2b_no_money_math_in_js():
    """C31-2 -- JS does no arithmetic on monetary-named variables.

    The audit's gate regex matches ``(act|est|amount|balance) [+-*/]
    (act|est|amount|balance)`` -- the JN-NN sites where the client
    recomputed a server-derivable figure.  Coding standard:
    ``Monetary values in JS are display-only.``
    """
    offenders: list[str] = []
    for path in _iter_files(JS_DIR, ".js"):
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _JS_MONEY_ARITHMETIC_RX.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Monetary arithmetic is forbidden in JS (MED-04 / E-17). "
        "Compute server-side in Decimal and emit the result via "
        "data-* attributes; JS should only render. Offending lines:\n"
        + "\n".join(offenders)
    )
