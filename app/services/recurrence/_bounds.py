"""
Shekel Budget App -- When a recurrence STOPS (plan step R7b-3)

A recurrence ends in exactly one of three ways and never in two: it runs
indefinitely, it stops ON A DATE, or it stops AFTER A COUNT of occurrences.
``budget.recurrence_rules`` records that as two nullable columns under
``ck_recurrence_rules_single_end_bound`` -- an EXCLUSIVE ARC, the shape plan
step X-au-c1 made structural for the pricing links one table over -- because
SQL has no sum type to write it in.  Python does, so above the columns the
bound is ONE value with three shapes and the illegal fourth state is
UNREPRESENTABLE rather than refused.

**That is not tidiness; it is what stops the count bound this step authors
turning an ordinary loan edit into a 500.**
``loan_recurrence_sync.sync_recurring_payment_bounds`` owns a loan payment's
closing bound -- the loan's derived payoff -- and states its change the way
every in-place writer in this package does, ``dataclasses.replace`` over the
rule's authored spec.  With two independent optional fields,
``replace(spec, end_date=payoff)`` leaves a count sitting beside the date it
just wrote, and ``ck_recurrence_rules_single_end_bound`` refuses the pair at
the flush.  With ONE field the same call replaces the WHOLE bound, so a count
cannot survive beside a date and there is no second rule for a writer to
remember.

**That crash was PROSPECTIVE, not measured, and the distinction matters.**
Before this step nothing wrote a count at all, so the pair was unreachable;
this step's own "Ends" control is what would have made it reachable.  Two
independent things now stop it, and only one of them is this module's: the
form door refuses a submitted bound on a loan payment
(``_recurrence_form_helpers.LOAN_PAYMENT_BOUND_IS_DERIVED``), which closes the
PATH, and the type closes the POSSIBILITY.  The second is what a later step
adding a third door inherits for free; the first is not.

**Two of the four CHECK constraints plan ledger row D23 names close here, with
no door refusal written for either.**  ``single_end_bound`` has no value in the
application that can break it -- what remains is
:func:`end_bound_from_columns`'s refusal of a stored pair, which is a READ of
untyped storage rather than a rule the writers restate -- and
``positive_max_occurrences`` is refused by :class:`EndsAfterOccurrences` at
construction, which is every path there is because a frozen dataclass cannot be
mutated past its own ``__post_init__``.  The other two, ``due_dom`` and
``valid_offset``, are column DOMAINS over plain integers rather than shapes, so
this step mirrors them at the door instead
(``_resolution._require_authored_domains``); making those structural means a
day-of-month VALUE TYPE, which is plan step G2's work and not this one's.

**The first ``abc.ABC`` in ``app/``, and that is a decision rather than an
accident.**  This package's precedent for a closed set with per-member
behaviour is table-driven (``_frequency.PATTERN_DERIVATIONS`` keyed by enum
member), and that shape fits a set whose members differ only in DATA.  These
three differ in BEHAVIOUR -- what stops the walk, what the columns are, what a
submission must state -- so a table would hold three callables per member and
lose the one thing an abstract method buys: a shape that forgets one is
unconstructible rather than wrong at first use.  On a closed set that decides
when a bill stops being charged, construction-time completeness is worth the
new pattern.

**No kind column, and ruling R-R13 already settled the shape for this table.**
Which of the three a row holds is decided by which column is non-NULL, exactly
as ``nominal_day``'s absence is what discriminates a cadence that fires on a
day of the month; naming it a third time would be the "second representation to
disagree with" that ruling refuses.

**The three shapes are a CLOSED set, stated once** (:data:`END_BOUND_KINDS`).
The form's offer set, the schema's accepted tokens and the composition a
submission goes through all read that tuple, so a shape this module does not
name is unofferable, unsubmittable and unconstructible together -- the same
property plan step R7b-2 gave the cadence controls by serving them from the
encoder's own table.

Pure: no Flask, no ORM, no clock, no database.
"""
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from itertools import islice
from typing import ClassVar

from app.exceptions import ShekelError
from app.services.recurrence._frequency import RecurrenceResolutionError


class EndBoundInputError(ShekelError):
    """A submitted closing bound named a shape without stating its value.

    **User input, and deliberately NOT
    :class:`~app.services.recurrence.RecurrenceResolutionError`** -- which this
    package documents as a broken invariant nobody flashes.  Choosing "on a
    date" and leaving the date blank is an ordinary mistake a form must report
    against the control the user left empty, so the error carries the field to
    attach it to and the schema layer turns it into a marshmallow
    ``ValidationError`` there.

    Refusing rather than defaulting is the point.  A blank date under "ends on
    a date" could be read as "runs indefinitely", and that reading would take a
    user who meant to STOP a recurring bill and silently leave it running --
    which is the disposition
    :func:`app.services.recurrence.EndsOnDate.from_payload` exists to avoid.

    **The user-facing sentences live with the RULE rather than with the
    display copy**, and an adversarial review of this step questioned that
    against ``_picker``'s and ``_describe``'s stated division ("the copy is
    this module's").  Those two own what a thing is CALLED -- a label, a
    phrase -- and this owns what a refusal SAYS, which is inseparable from the
    condition that raises it.  The package's precedent is
    :data:`~app.services.recurrence.UNAVAILABLE_PATTERN_MESSAGE`, user-facing
    copy in ``_vocabulary`` beside the membership test that earns it, flashed
    by ``_recurrence_form_helpers.edit_form_cadence``.

    Attributes:
        field: The form field the refusal belongs to, so the surface can mark
            the control the user actually left empty rather than the mode
            select they answered correctly.
        message: The sentence to show.  Held on the exception rather than
            composed by the catcher, because which control is at fault and
            what to say about it are one fact.
    """

    def __init__(self, field: str, message: str) -> None:
        """Record which control is at fault and what to say about it.

        Args:
            field: The form field name to attach the refusal to.
            message: The user-facing sentence.
        """
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True)
class EndBoundColumns:
    """The two ``budget.recurrence_rules`` columns a bound writes.

    The STORAGE projection of an :class:`EndBound`, produced by
    :meth:`EndBound.columns` and consumed by
    ``app.services.recurrence._authoring._author`` -- the one function in the
    application that assigns a column of that table.

    A named pair rather than a bare tuple because the write door assigns the
    two by name, and rather than two separate accessors because they are ONE
    value seen from the storage side: a shape that produced them independently
    could produce a pair the CHECK refuses, which is the state this whole
    module exists to make unconstructible.

    Attributes:
        end_date: What ``recurrence_rules.end_date`` gets, or ``None``.
        max_occurrences: What ``recurrence_rules.max_occurrences`` gets, or
            ``None``.  Never non-``None`` at the same time as *end_date*: no
            :class:`EndBound` shape emits both, which is
            ``ck_recurrence_rules_single_end_bound`` held by construction.
    """

    end_date: date | None
    max_occurrences: int | None


class EndBound(ABC):
    """When a recurrence stops -- one value, three shapes, never two answers.

    See the module docstring for why this is a sum type rather than the pair of
    nullable fields the columns are.

    **Every shape answers all three questions, and the base implements none of
    them.**  A default here -- "a bound that does not recognise the question
    runs forever" -- is the partial-function-over-a-closed-set defect this
    whole arc exists to remove: a shape added for plan step R8 and left
    half-written would read as "this never ends", which on a financial surface
    is a commitment the app goes on charging forever.  ``@abstractmethod``
    makes that shape unconstructible instead of merely wrong.

    :attr:`end_date` and :attr:`max_occurrences` are DERIVED from
    :meth:`columns` rather than being abstract in their own right, so a new
    shape states its storage once.  They exist for ONE consumer -- the edit
    form's prefill, which genuinely has two inputs to fill and so genuinely
    wants the pair -- and asking a TEMPLATE to unwrap
    :class:`EndBoundColumns` would put a lookup in the layer least able to
    afford it (the same reasoning
    :class:`~app.services.recurrence.CadenceOption` records).  An earlier note
    here claimed three consumers; an adversarial review measured one, the
    display cell having moved to a worded phrase in this same step.

    Attributes:
        token: What this shape's ``<option>`` posts, and the key
            :func:`end_bound_from_token` dispatches on.  A ``ClassVar`` on the
            shape itself rather than an entry in a table beside it: a shape and
            the value that names it cannot then be added apart.
    """

    token: ClassVar[str]

    @abstractmethod
    def columns(self) -> EndBoundColumns:
        """Return the two column values this bound writes.

        Returns:
            The :class:`EndBoundColumns` for this shape.
        """

    @property
    def end_date(self) -> date | None:
        """Return the date this bound stops on, or ``None``.

        Returns:
            The date, or ``None`` for a shape that names none.
        """
        return self.columns().end_date

    @property
    def max_occurrences(self) -> int | None:
        """Return the count this bound stops after, or ``None``.

        Returns:
            The count, or ``None`` for a shape that names none.
        """
        return self.columns().max_occurrences

    @abstractmethod
    def admits(self, *, emitted: int, occurrence: date) -> bool:
        """Return whether an occurrence walk may still emit *occurrence*.

        **Every occurrence walk in this package is ASCENDING, so the first
        ``False`` is also the last one worth asking about**: a caller STOPS
        rather than skipping, and a shape that answered ``False`` for one
        occurrence and ``True`` for a later one would be a bound that reopens.
        Stated here because it is a contract over all three shapes rather than
        a property of any one of them.

        Args:
            emitted: How many occurrences the walk has already yielded.  The
                count a count-bounded rule is measured against -- occurrences
                the CADENCE names, including any the schedule never places
                (ruling R-R6).
            occurrence: The date being considered.

        Returns:
            ``True`` while the bound is still open, ``False`` at the first
            occurrence past it.
        """

    @abstractmethod
    def has_closed(
        self,
        *,
        on: date,
        occurrences_before: Callable[[], Iterator[date]],
    ) -> bool:
        """Return whether this bound had already stopped the rule before *on*.

        The question ``obligations_aggregator`` asks to decide whether a
        recurring commitment is still a FUTURE obligation.  It replaced a
        direct ``rule.end_date < as_of`` read, which had no answer for a count
        bound at all -- so a spent count would have gone on inflating
        ``/obligations`` and the ``/savings`` emergency-fund baseline forever
        while the same row's "Next" column, which walks occurrences, showed
        blank.

        **The occurrence walk arrives as a CALLABLE rather than as an
        iterator, and the reason is cost rather than correctness.**  Two of the
        three shapes are pure comparisons that never read it, so passing an
        iterator would make every caller resolve a rule against its owner's
        schedule for the single shape that needs one --
        ``obligations_aggregator`` asks this per recurring template, so that is
        a schedule load per row.  It does NOT change any shape's answer: an
        earlier draft of this paragraph claimed it would for an owner with no
        pay periods, and an adversarial review measured that false in both
        directions -- :meth:`EndsOnDate.has_closed` never reads the argument,
        and ``rule_occurrences`` answers ``()`` rather than raising for an
        empty schedule (``_reading.resolved_recurrence``).

        Args:
            on: The day being asked about, normally "today".
            occurrences_before: Called with no arguments to obtain this rule's
                own occurrences that fall STRICTLY BEFORE *on*, ascending.
                **Strictly**, which the caller has to arrange: ``occurrences``
                takes an INCLUSIVE ``through``, so the natural ``through=on``
                is off by one and would report a bound closed one occurrence
                early.  ``_reading.has_ended`` is the one caller and passes
                ``on - 1 day``.  The sequence is already bounded by this same
                value, so a count-bounded rule yields at most its own count.

        Returns:
            ``True`` when the rule names no further occurrence on or after
            *on* BY ITS OWN BOUND.  A rule the schedule has simply not been
            extended far enough to reach answers ``False``: that is a schedule
            that has not got there yet, not a commitment that ended.
        """

    @classmethod
    @abstractmethod
    def from_payload(
        cls, *, end_date: date | None, max_occurrences: int | None,
    ) -> "EndBound":
        """Build this shape from the form's two optional bound inputs.

        The half of the form-to-domain conversion that only the shape can do:
        which of the two inputs this shape needs, and what to say when it is
        blank.  :func:`end_bound_from_token` picks the shape; this fills it.

        Args:
            end_date: The submitted date, or ``None``.
            max_occurrences: The submitted count, or ``None``.

        Returns:
            The constructed bound.

        Raises:
            EndBoundInputError: This shape needs an input the submission did
                not state.
        """


@dataclass(frozen=True)
class NeverEnds(EndBound):
    """The recurrence runs indefinitely -- both columns NULL.

    The default for every rule the application authors without being told
    otherwise, and what 41 of the 46 live production rules carry (measured
    2026-08-13).
    """

    token: ClassVar[str] = "never"

    def columns(self) -> EndBoundColumns:
        """Return both columns NULL.

        Returns:
            The :class:`EndBoundColumns` for an unbounded rule.
        """
        return EndBoundColumns(end_date=None, max_occurrences=None)

    def admits(self, *, emitted: int, occurrence: date) -> bool:
        """Admit every occurrence.

        Args:
            emitted: Unread -- an unbounded rule counts nothing.
            occurrence: Unread.

        Returns:
            Always ``True``.
        """
        return True

    def has_closed(
        self,
        *,
        on: date,
        occurrences_before: Callable[[], Iterator[date]],
    ) -> bool:
        """Never close.

        Args:
            on: Unread.
            occurrences_before: Never called -- see
                :meth:`EndBound.has_closed` for why it is a callable.

        Returns:
            Always ``False``.
        """
        return False

    @classmethod
    def from_payload(
        cls, *, end_date: date | None, max_occurrences: int | None,
    ) -> "NeverEnds":
        """Return the unbounded shape, ignoring both inputs.

        A submission naming this shape has both inputs DISABLED, so the form
        posts neither; a hand-assembled POST that states one anyway is
        answered honestly as what it said it was, rather than being refused
        for a field the user's choice does not use.

        Args:
            end_date: Unread.
            max_occurrences: Unread.

        Returns:
            :class:`NeverEnds`.
        """
        return cls()


@dataclass(frozen=True)
class EndsOnDate(EndBound):
    """The recurrence stops after a stated date.

    Attributes:
        on: The last day an occurrence may fall on.  Bounds the OCCURRENCE and
            not the pay period it lands in (ruling R-R6): the reverse matcher
            this replaced tested a period's START, so a monthly-15th rule
            ending 2025-06-05 generated a row due 2025-06-15 (plan defect D5).
    """

    token: ClassVar[str] = "on_date"

    on: date

    def columns(self) -> EndBoundColumns:
        """Return the date column set and the count column NULL.

        Returns:
            The :class:`EndBoundColumns` for a date-bounded rule.
        """
        return EndBoundColumns(end_date=self.on, max_occurrences=None)

    def admits(self, *, emitted: int, occurrence: date) -> bool:
        """Admit occurrences up to and including :attr:`on`.

        Args:
            emitted: Unread -- a date bound counts nothing.
            occurrence: The date being considered.

        Returns:
            ``True`` while *occurrence* is on or before :attr:`on`.
        """
        return occurrence <= self.on

    def has_closed(
        self,
        *,
        on: date,
        occurrences_before: Callable[[], Iterator[date]],
    ) -> bool:
        """Return whether the bound date is already past.

        **Byte-identical to the ``rule.end_date < as_of`` test this replaced**,
        which is what makes the aggregator's filter provably unmoved for every
        rule that exists today: five live rules carry a date bound and none
        carries a count.

        It is CONSERVATIVE, and deliberately so: a bound is not a last
        occurrence, so a rule bounded 2026-12-31 that fires each January is
        still counted as a live obligation through that December.  Narrowing
        it to "names no further occurrence" would be a different figure on two
        money surfaces, which is not this step's to change.

        Args:
            on: The day being asked about.
            occurrences_before: Never called.

        Returns:
            ``True`` when :attr:`on` is strictly before the day asked about.
        """
        return self.on < on

    @classmethod
    def from_payload(
        cls, *, end_date: date | None, max_occurrences: int | None,
    ) -> "EndsOnDate":
        """Build a date bound, refusing a blank date.

        Args:
            end_date: The submitted date.
            max_occurrences: Unread -- this shape's own input is the date.

        Returns:
            The :class:`EndsOnDate`.

        Raises:
            EndBoundInputError: No date was submitted.
        """
        if end_date is None:
            raise EndBoundInputError(
                "end_date",
                "Choose the date this stops repeating, or set it to never end.",
            )
        return cls(on=end_date)


@dataclass(frozen=True)
class EndsAfterOccurrences(EndBound):
    """The recurrence stops after a stated number of occurrences.

    **The count is of occurrences the CADENCE names**, including any the saved
    schedule does not reach and never places (ruling R-R6): "stop after twelve"
    is a property of the rule, not of how many rows the schedule happened to
    host.

    Attributes:
        count: How many occurrences the rule fires.  At least 1 --
            :meth:`__post_init__` refuses anything else, which is
            ``ck_recurrence_rules_positive_max_occurrences`` held by
            construction rather than mirrored at the door.
    """

    token: ClassVar[str] = "after_occurrences"

    count: int

    def __post_init__(self) -> None:
        """Refuse a count that names no occurrence.

        The whole of ``ck_recurrence_rules_positive_max_occurrences``, in the
        only place a count bound can come into existence.  A frozen dataclass
        cannot be mutated afterwards, so there is no second path to police --
        which is the difference between this and a refusal at the write door,
        where the value already exists by the time it is inspected.

        Raises:
            EndBoundInputError: *count* is below 1.  A user-input refusal
                rather than a broken invariant because the form's number box
                is where a 0 comes from; the box also carries ``min="1"``, and
                a browser that honours it is not a validator.
        """
        if self.count < 1:
            raise EndBoundInputError(
                "max_occurrences",
                f"Enter how many times this repeats -- at least 1, not "
                f"{self.count}.",
            )

    def columns(self) -> EndBoundColumns:
        """Return the count column set and the date column NULL.

        Returns:
            The :class:`EndBoundColumns` for a count-bounded rule.
        """
        return EndBoundColumns(end_date=None, max_occurrences=self.count)

    def admits(self, *, emitted: int, occurrence: date) -> bool:
        """Admit occurrences until :attr:`count` have been emitted.

        Args:
            emitted: How many the walk has already yielded.
            occurrence: Unread -- a count bound names no date.

        Returns:
            ``True`` while fewer than :attr:`count` have been emitted.
        """
        return emitted < self.count

    def has_closed(
        self,
        *,
        on: date,
        occurrences_before: Callable[[], Iterator[date]],
    ) -> bool:
        """Return whether all :attr:`count` occurrences fell before *on*.

        The one shape that needs the schedule, because when the count is spent
        depends on when the occurrences fall -- and for a paycheck-space rule
        those ARE the owner's paydays.

        A schedule that reaches fewer than :attr:`count` occurrences answers
        ``False``: the remaining ones have not happened yet, so the commitment
        is still live.  That is the same conservatism the date shape shows, and
        it is what stops an un-extended pay schedule from silently dropping a
        live obligation out of two money totals.

        Args:
            on: The day being asked about.
            occurrences_before: Called once for this rule's occurrences
                strictly before *on*.

        Returns:
            ``True`` when the walk yields the full count before *on*.
        """
        before = islice(occurrences_before(), self.count)
        return sum(1 for _ in before) >= self.count

    @classmethod
    def from_payload(
        cls, *, end_date: date | None, max_occurrences: int | None,
    ) -> "EndsAfterOccurrences":
        """Build a count bound, refusing a blank count.

        Args:
            end_date: Unread -- this shape's own input is the count.
            max_occurrences: The submitted count.

        Returns:
            The :class:`EndsAfterOccurrences`.

        Raises:
            EndBoundInputError: No count was submitted, or it is below 1
                (raised by :meth:`__post_init__`).
        """
        if max_occurrences is None:
            raise EndBoundInputError(
                "max_occurrences",
                "Enter how many times this repeats, or set it to never end.",
            )
        return cls(count=max_occurrences)


#: The closed set of shapes a closing bound can take, in the order a form
#: offers them.
#:
#: **Stated once and read by everything that has an opinion about the set**:
#: the picker's options (``_picker.end_bound_options``), the schema's accepted
#: tokens, and :func:`end_bound_from_token`'s dispatch.  A shape absent here is
#: unofferable, unsubmittable and unreachable together, which is the property
#: plan step R7b-2 gave the cadence controls by serving them from the encoder's
#: own table -- a refusal made unreachable rather than fenced.
END_BOUND_KINDS: tuple[type[EndBound], ...] = (
    NeverEnds,
    EndsOnDate,
    EndsAfterOccurrences,
)

#: :data:`END_BOUND_KINDS` keyed by the token its shape posts.  Inverted from
#: the tuple rather than written out, for the reason
#: ``_frequency._PATTERNS_BY_READING`` is inverted from its forward table: a
#: second hand-written mapping fails in the direction nobody tests -- an entry
#: changed on one side only.
_KINDS_BY_TOKEN: dict[str, type[EndBound]] = {
    kind.token: kind for kind in END_BOUND_KINDS
}

#: The bound every rule carries unless something states otherwise.
#:
#: A shared instance rather than a constructor call at each site: it is frozen
#: and field-less, so all its instances are equal and one is enough -- and it
#: reads as the value it is at a call site (``end_bound=NEVER_ENDS``) rather
#: than as construction.
NEVER_ENDS: NeverEnds = NeverEnds()


def end_bound_from_columns(
    end_date: date | None, max_occurrences: int | None,
) -> EndBound:
    """Return the bound a stored row's two columns name.

    The READ half of the storage split, and the inverse of
    :meth:`EndBound.columns`.  Every reader of
    ``budget.recurrence_rules.end_date`` / ``.max_occurrences`` above the write
    door goes through it, so "which shape is this row" is decided once.

    Args:
        end_date: The row's ``end_date``.
        max_occurrences: The row's ``max_occurrences``.

    Returns:
        The :class:`EndBound` the pair names -- :data:`NEVER_ENDS` when both
        are NULL, which is the exclusive arc's fourth and legal state.

    Raises:
        RecurrenceResolutionError: Both columns are set, or the count is below
            1.  Each is a row written around a CHECK that refuses it in the
            table (``ck_recurrence_rules_single_end_bound``,
            ``ck_recurrence_rules_positive_max_occurrences``), so each is a
            BROKEN INVARIANT here rather than the user-input refusal the same
            values earn at the form -- which is why the count refusal is
            translated rather than restated: the rule is
            :meth:`EndsAfterOccurrences.__post_init__`'s, and only its MEANING
            changes with the side of the door it is read from.

            Both are refusals rather than preferences.  Picking one of two
            bounds, or reading a 0 as "does not stop", would keep charging a
            bill past a stop the user set.
    """
    if end_date is not None and max_occurrences is not None:
        raise RecurrenceResolutionError(
            f"a recurrence rule states two closing bounds: end_date "
            f"{end_date} AND max_occurrences {max_occurrences}.  A rule has at "
            f"most one (ck_recurrence_rules_single_end_bound), so this row was "
            f"written around that constraint; picking one of the two would "
            f"answer with a stop date the user never set."
        )
    if end_date is not None:
        return EndsOnDate(on=end_date)
    if max_occurrences is None:
        return NEVER_ENDS
    try:
        return EndsAfterOccurrences(count=max_occurrences)
    except EndBoundInputError as exc:
        raise RecurrenceResolutionError(
            f"a recurrence rule states a count bound of {max_occurrences}, "
            f"which names no occurrence.  "
            f"ck_recurrence_rules_positive_max_occurrences refuses it in the "
            f"table, so this row was written around that constraint."
        ) from exc


def end_bound_from_token(
    token: str,
    *,
    end_date: date | None,
    max_occurrences: int | None,
) -> EndBound:
    """Return the bound a submitted form states.

    The WRITE half of the form-to-domain conversion, total over
    :data:`END_BOUND_KINDS`.  It picks the shape; the shape's own
    :meth:`EndBound.from_payload` fills it and says what is missing.

    **The token set is not validated separately**, and that is deliberate: a
    ``OneOf`` beside this dispatch would be a second statement of which shapes
    exist, and the two could then disagree in the direction where one accepts
    what the other cannot build.  The dispatch IS the validation.

    Args:
        token: The submitted mode value -- an :attr:`EndBound.token`.
        end_date: The submitted date input, or ``None``.
        max_occurrences: The submitted count input, or ``None``.

    Returns:
        The :class:`EndBound` the submission names.

    Raises:
        EndBoundInputError: *token* names no shape, or the named shape's own
            input was not stated.
    """
    kind = _KINDS_BY_TOKEN.get(token)
    if kind is None:
        raise EndBoundInputError(
            "recurrence_end_mode",
            "Choose when this stops repeating.",
        )
    return kind.from_payload(
        end_date=end_date, max_occurrences=max_occurrences,
    )


__all__ = [
    "END_BOUND_KINDS",
    "NEVER_ENDS",
    "EndBound",
    "EndBoundColumns",
    "EndBoundInputError",
    "EndsAfterOccurrences",
    "EndsOnDate",
    "NeverEnds",
    "end_bound_from_columns",
    "end_bound_from_token",
]
