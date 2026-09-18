"""
Shekel Budget App -- A STATED figure and who stated it

WHAT figure a door hands a settle verb and WHO WROTE it, as ONE value (plan
step **balance:X-bi-3e-1**, rulings **R-BAL61** and **R-BAL69**).  A movement's
``figure_source_id`` records who wrote its figure -- a person (``typed``) or
the bank's own line (``observed``) -- and until this step the status seam
INFERRED that answer from the day's basis beside the figure
(``settle_day.figure_source_of``, deleted here): a figure a person typed over
a standing bank-observed day was labelled the bank's, and a hand-typed
purchase the bank confirmed on a day-only match was relabelled ``observed``.
Ruling R-BAL61 measured that premise false on every row kind and ruled that
the WRITER states the source.  This value is how a writer states it.

**It is a VALUE TYPE rather than a second parameter, on the argument
:class:`~app.services.settle_day.SettleDay` already made for the DAY one
column over (plan step X-az).**  A loose ``source`` beside an optional
``submitted`` figure would be two facts with a pairing rule between them --
a source with no figure, a figure with no source -- that every verb would
have to refuse; with one value there is nothing to pair.  A door that hands
a verb a figure says who wrote it, or it hands over nothing at all, which is
what every parameter typed ``StatedFigure | None`` means by ``None``:
*nobody stated a figure*, and the settle records what it resolved.

**Why a shared leaf rather than a home in either door's package.**  Both
``budget.transactions`` (through the status seam's settle verbs) and
``budget.transaction_entries`` (through ``entry_service``'s purchase doors)
take a stated figure, and ``status_seam``'s import closure already reaches
``entry_service`` -- so a value living in the seam would put the purchase
doors one import away from a cycle.  The same placement argument put
:class:`~app.services.settle_day.SettleDay` in its own leaf.

Pure: a frozen value and one refusal.  No session, no ``ref_cache``, no Flask.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.enums import MovementFigureSourceEnum


@dataclass(frozen=True)
class StatedFigure:
    """A figure a door states, and WHO WROTE it.

    Attributes:
        amount: The figure.  Never ``None`` -- a door with no figure passes no
            :class:`StatedFigure` at all, which is what the ``| None`` on every
            parameter typed with it means.  Its SIGN is the door's contract:
            a settle verb takes a magnitude, a purchase door a signed figure
            (negative for a refund, ruling **bank_import:R-II**).
        source: Who wrote it (:class:`app.enums.MovementFigureSourceEnum`):
            ``typed`` for a person -- the popover, the reconcile panel, the
            entry PATCH -- and ``observed`` for the bank's own line, which the
            statement matcher alone states.

    **``resolved`` is refused at construction.**  That member names the
    settle's OWN pricing of the plan, which no door can state: a door either
    hands over a figure somebody stated or hands over nothing and lets the
    verb resolve.  A ``StatedFigure`` claiming ``resolved`` would be a door
    laundering its own number as the app's inference, so the value cannot be
    built -- the same reason a :class:`~app.services.settle_day.SettleDay`
    refuses to wrap ``None``.
    """

    amount: Decimal
    source: MovementFigureSourceEnum

    def __post_init__(self) -> None:
        """Refuse a value no door may state.

        Raises:
            ValueError: When :attr:`amount` is ``None`` (a door that meant to
                pass no value wrapped one instead), or when :attr:`source` is
                ``resolved`` (the settle's own arm, which is not a statement).
                Programming errors at the call site rather than user errors,
                so neither is a ``ValidationError``: no form can express
                either state.
        """
        if self.amount is None:
            raise ValueError(
                "A StatedFigure states a figure, so it cannot wrap None. A "
                "door with no figure passes None in place of the whole value, "
                "which is what every parameter typed 'StatedFigure | None' "
                "means by it."
            )
        if self.source is MovementFigureSourceEnum.RESOLVED:
            raise ValueError(
                "A StatedFigure cannot claim the 'resolved' source: that "
                "member names the settle's own pricing of the plan, which no "
                "door states. Hand the verb None and it resolves the figure "
                "itself, or state who actually wrote this one ('typed' or "
                "'observed')."
            )
