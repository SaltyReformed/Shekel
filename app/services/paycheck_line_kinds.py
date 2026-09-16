"""Shekel Budget App -- What a payroll line's KIND means, stated once.

Plan step **salary:R18-b** (ruling **R-SAL38**; ledger row **D59**): a
paycheck is base pay plus a list of lines, and a line's kind is its position
in the waterfall.  The engine reads the kind as an id
(:mod:`app.services.paycheck_calculator._lines`); everything that WORDS a
kind or asks which SIDE it is on reads it here, so the four labels, the
two sides and the select's offer set have one home rather than a spelling
per template.

* :data:`LABELS` -- the label each kind renders as ("Taxable earning",
  "Pre-tax deduction", ...), the one spelling the salary page's chip, its
  kind select (through :func:`kind_options`) and the cockpit's anatomy
  groups all read.  Until R18-b the surfaces derived a label from the ref
  row's name (``pre_tax_deduction`` -> "Pre-Tax-Deduction"), the second of
  R18-a's three accepted display changes; this is where R18-b re-words them.
* :func:`is_deduction` -- which side a kind is on.  A DEDUCTION may name the
  account it funds (``target_account_id``, the contribution feed's hook);
  an earning is money IN and funds nothing, so the schema refuses a target
  on an earning kind through this predicate.
* :func:`kind_options` -- the select's ``(id, label)`` pairs in waterfall
  order, the enum's own order.

Flask-free: reads the ref cache and returns plain values.
"""

from app import ref_cache
from app.enums import PaycheckLineKindEnum

#: The label each kind renders as, keyed by member.  Spelled once; the
#: waterfall order is the enum's.
LABELS: dict[PaycheckLineKindEnum, str] = {
    PaycheckLineKindEnum.TAXABLE_EARNING: "Taxable earning",
    PaycheckLineKindEnum.PRE_TAX_DEDUCTION: "Pre-tax deduction",
    PaycheckLineKindEnum.POST_TAX_DEDUCTION: "Post-tax deduction",
    PaycheckLineKindEnum.AFTER_TAX_EARNING: "After-tax earning",
}

#: The deduction side: the kinds that LEAVE the paycheck and may fund an account.
DEDUCTION_KINDS: frozenset[PaycheckLineKindEnum] = frozenset({
    PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
    PaycheckLineKindEnum.POST_TAX_DEDUCTION,
})

#: The one refusal an earning kind has: a target account.  Spelled once for
#: the two doors that state it -- the schema over a POSTED pair, the update
#: route over the EFFECTIVE pair (a posted kind beside a STORED target the
#: payload left alone).
EARNING_TARGET_REFUSAL = (
    "Only a deduction can fund an account; an earning line is paid to you "
    "and has no target account."
)


def is_deduction(kind_id: int) -> bool:
    """Whether *kind_id* is one of the two deduction kinds.

    Args:
        kind_id: A stored or submitted ``paycheck_line_kind_id``.

    Returns:
        ``True`` for a pre-tax or post-tax deduction; ``False`` for an earning
        kind, and for an id the cache does not hold (which the FK refuses).
    """
    return ref_cache.paycheck_line_kind_member(kind_id) in DEDUCTION_KINDS


def kind_options() -> list[tuple[int, str]]:
    """Return every kind as ``(id, label)``, in waterfall order, for a select.

    Returns:
        The four pairs, taxable earning first and after-tax earning last.
    """
    return [
        (ref_cache.paycheck_line_kind_id(member), LABELS[member])
        for member in PaycheckLineKindEnum
    ]


__all__ = [
    "DEDUCTION_KINDS", "EARNING_TARGET_REFUSAL", "LABELS", "is_deduction", "kind_options",
]
