"""Shekel Budget App -- What a pay stub's TAX is called, stated once.

Plan step **salary:S11-b** (ruling **R-SAL42**): a transcribed pay stub holds
one amount per tax it prints, keyed by ``ref.withholding_kinds``
(:class:`~app.enums.WithholdingKindEnum`), and the reference table carries no
display column by design -- the label is presentation, and it lives here, the
shape :mod:`app.services.paycheck_line_kinds` gives the paycheck-line kinds.
Two readers word a tax through it: the entry screen (its four inputs and the
saved stub's figures) and the name-clash rule (ruling **R-SAL45**, "a one-off
named like ... a tax" is refused), which compares a one-off's name against
exactly these labels.

Flask-free: reads the ref cache and returns plain values.
"""

from app import ref_cache
from app.enums import WithholdingKindEnum

#: The label each tax renders as, keyed by member.  Spelled once; the order a
#: stub lists its taxes in is the enum's.
LABELS: dict[WithholdingKindEnum, str] = {
    WithholdingKindEnum.FEDERAL_INCOME: "Federal income tax",
    WithholdingKindEnum.STATE_INCOME: "State income tax",
    WithholdingKindEnum.SOCIAL_SECURITY: "Social Security",
    WithholdingKindEnum.MEDICARE: "Medicare",
}


def kind_options() -> list[tuple[int, str]]:
    """Return every tax as ``(id, label)``, in the enum's order.

    Returns:
        The four pairs, federal income tax first and Medicare last.
    """
    return [
        (ref_cache.withholding_kind_id(member), LABELS[member])
        for member in WithholdingKindEnum
    ]


__all__ = ["LABELS", "kind_options"]
