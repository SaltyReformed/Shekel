"""What an account's books cannot explain, and what has CHECKED those days.

Plan step **balance:X-f3c-3** (``docs/audits/balance_architecture/README.md``
section 5).  Two facts that only mean anything together, resolved in ONE call:

* the OUTSTANDING DIFFERENCE -- the owner's latest declared balance less what
  the account's books produce for that same day, which is
  :func:`app.services.balance_at.cash_outstanding_difference`;
* whether an imported bank statement RECONCILES the days that difference
  accumulated over, which is a reading of
  :func:`app.services.bank_agreement.bank_agreement`.

**Money-neutral.**  Nothing here writes, and nothing here offers an act: the
figure is the INSTRUMENT plan step X-f3c-4 needs, and the act that ACCEPTS it
as an ordinary uncategorized transaction is that step (ruling **R-FN**).

**Why the two travel together, and why that is this module rather than the
route.**  A difference the books cannot explain means one thing over a span the
bank has confirmed line by line and quite another over a span nobody has
imported -- ruling **R-GY** turns exactly that into X-f3c-4's offer gate.  The
span the verdict must be about is the DIFFERENCE's own
(:class:`~app.services.balance_at.BooksSpan`, carried on the difference), so a
caller that resolved the two halves separately would hold a figure and a span
as independent arguments and could pair them wrongly -- the shape finding
**N-354** closed one layer down.  One door, one account, nothing left to pair.

**Its own module rather than four more values in** :mod:`.bank_agreement`,
and the reason is measured rather than tidy: that module stood at 797 lines of
pylint's 1000-line ceiling before this step and the first build of this leaf
put it at **993**, seven lines of headroom for whoever touches it next.
Findings **N-152**, **N-156** and **N-201** record the same ceiling on three
other service modules and rule the same answer -- a split on the seam, never
another round of shaving prose off a measured claim.  The seam is real: that
module owns the per-DAY comparison, and this owns a reading of it beside a
figure from a different package.

Services-boundary discipline: no Flask import, no clock read -- the reader's
NOW arrives on the :class:`~app.services.balance_at.BalanceContext`.  Reads
only; no writes, no commit.
"""

from dataclasses import dataclass
from datetime import date

from app.models.account import Account
from app.services import balance_at, bank_agreement


@dataclass(frozen=True)
class SpanAgreement:
    """Whether the bank's own record accounts for one span of days.

    A reading of :class:`~app.services.bank_agreement.BankAgreement` narrowed
    to the days one figure accumulated over.

    **It REPORTS and it refuses nothing**, which is ruling **R-GF** applied to
    a narrower question than the whole comparison page: what GATES the
    acceptance act is X-f3c-4's decision (ruling **R-GY**), taken where the
    money moves, and this value is what that decision reads rather than the
    decision itself.

    **The test is per DAY, never on the net.**  That is the same measurement
    :attr:`~app.services.bank_agreement.AgreementDay.agrees` rests on: on the
    developer's Checking account 11 of 35 real disagreements read as EXACT
    agreement in the balance difference, because a same-day assertion cancels
    the error to the cent.  So what this value counts is DAYS, and it carries
    no summed figure at all.

    Attributes:
        first_day: The span's first day, echoed back so a reader holding this
            value alone knows what it is about.
        last_day: The span's last day.
        day_count: How many days the span holds -- ``0`` for an inverted span,
            which is what :attr:`~app.services.balance_at.BooksSpan.is_empty`
            describes.
        compared: How many of those days the comparison could actually take.
            Fewer than :attr:`day_count` for a day outside the drawn range
            (``bank_agreement``'s two-year bound, or past the reader's NOW) and
            for a day before the app's own records begin, where a zero
            ``recorded`` means *nothing recorded* rather than *nothing
            happened* (finding **N-314**).
        disagreeing: How many of the COMPARED days the two records differ on.

            *A signed net RESIDUE over those days was published beside this
            until adversarial review 2026-09-01 and is gone, for two reasons
            that point the same way: nothing in* ``app/`` *read it, and a
            SIGNED sum is the shape*
            :attr:`~app.services.bank_agreement.BankAgreement.asserted_total`
            *warns understates its own subject -- two disagreements of
            opposite sign read as* ``$0.00``.  *A reader wanting the size
            opens the day-by-day comparison, which states it per day.*
        imported: How many days of the span are SPANNED BY THE LINES of an
            import -- between some recorded statement's own first and last line
            day, merged across overlapping and adjacent imports
            (:func:`app.services.statement_import.covered_runs`, carried on the
            report as :attr:`~app.services.bank_agreement.BankAgreement.imports`).
            A day outside every such run is a day the app holds no bank record
            near: the bank may have posted lines there and nothing would show
            it, so its residue is ``0.00`` and it "agrees" vacuously.  That is
            the one hole no count over the report's own days can see, because a
            quiet day inside a run and a quiet day outside one look identical.

            **It is NOT "a day a statement was read", and the difference is
            measured rather than pedantic.**  ``statement_imports.period_start``
            / ``period_end`` are written as ``min``/``max`` of the FILE'S LINE
            DAYS (``statement_import._record``), and no adapter records the
            period a file DECLARES -- ``ParsedStatement`` carries the header's
            balance and not its date range.  So a March statement whose first
            transaction is the 5th and last is the 28th is recorded as covering
            2026-03-05..2026-03-28, and this count calls March 1st-4th
            un-imported although that statement read them.  The consequence is
            that this term is CONSERVATIVE: it refuses spans the bank has in
            fact accounted for, never the reverse, which is the safe direction
            for a value ruling **R-GY** turns into a money-moving act's gate.
            Recording the period a file declares is finding **N-434**, on the
            ``bank_import`` arc, and it is what would make this exact.
    """

    first_day: date
    last_day: date
    day_count: int
    compared: int
    disagreeing: int
    imported: int

    @property
    def unchecked(self) -> int:
        """Return how many days of the span nothing compared.

        Returns:
            ``day_count - compared``, which is ``0`` exactly when every day of
            the span was compared.
        """
        return self.day_count - self.compared

    @property
    def unimported(self) -> int:
        """Return how many days of the span no import covers.

        Returns:
            ``day_count - imported``, which is ``0`` exactly when the whole
            span lies inside one run of imported days.
        """
        return self.day_count - self.imported

    @property
    def reconciles(self) -> bool:
        """Return whether the bank's record accounts for EVERY day of the span.

        Four conditions, and each is a distinct way the claim could be empty:
        the span holds a day at all; the bank's own imports cover every one of
        them (:attr:`unimported` is zero); the comparison took every one
        (:attr:`unchecked` is zero); and none of the compared days disagreed.

        **A vacuous truth is not reconciliation.**  An empty span satisfies "no
        day disagrees" for free, and so does a span of days nobody imported --
        which is exactly the shape that would let an owner accept a difference
        over months nothing has ever read.

        **The two zero-counts are independent and neither implies the other,
        and they OVERLAP rather than partition.**  A day inside an imported run
        but before the app's own records begin is IMPORTED and not COMPARED
        (finding **N-314**); a day the report drew, sitting between two
        imports' line runs, is COMPARED and not IMPORTED; a day before every
        recorded line is NEITHER, and is counted by both.  A surface adding
        :attr:`unchecked` to :attr:`unimported` would therefore double-count,
        which is why the card states each as its own sentence about the whole
        span rather than as parts of a total.
        A first draft tested the imports' two END DAYS instead, and that term
        was DEAD: ``bank_agreement`` draws only days between the first and last
        recorded line, so ``unchecked == 0`` already implied it and it could
        never fire.  A defensive term that cannot fire is the *born dead* shape
        ``lessons.md`` names, and it reads as protection nobody has.

        Returns:
            True when the bank's own record accounts for every day the
            difference accumulated over.
        """
        return (
            self.day_count > 0
            and self.unimported == 0
            and self.unchecked == 0
            and self.disagreeing == 0
        )


@dataclass(frozen=True)
class OutstandingDifference:
    """One account's unexplained difference, beside what has checked its span.

    The output of :func:`outstanding_difference`, and the whole of what plan
    step X-f3c-4 needs before it may offer to book the figure.

    Attributes:
        difference: The
            :class:`~app.services.balance_at.CashOutstandingDifference` -- both
            sides of the subtraction and the span it accumulated over.
        reconciliation: The :class:`SpanAgreement` for that span, or ``None``
            when the account holds no recorded bank line AT ALL.  An absence
            rather than an empty comparison, which is the same distinction
            :func:`~app.services.bank_agreement.bank_agreement` answers ``None``
            for: "nobody has imported a statement" and "the statements say
            nothing disagrees" are different answers and a surface must not
            print the second for the first.
    """

    difference: "balance_at.CashOutstandingDifference"
    reconciliation: "SpanAgreement | None"


def outstanding_difference(
    account: Account, ctx: balance_at.BalanceContext,
) -> "OutstandingDifference | None":
    """Return what *account*'s books cannot explain, and what has checked it.

    Args:
        account: The account to measure.  Must belong to ``ctx.user_id``, which
            the read pass REFUSES rather than trusts.  Must be attached to
            ``db.session``.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The :class:`OutstandingDifference`, or ``None`` where the question does
        not apply at all -- an account whose balance carries a MODELLED tier
        (ruling **R-FO**: an IRA has no record of a price movement to discard,
        so the same subtraction there is its RETURN, finding **N-213**), and an
        account carrying no assertion for its books to disagree with.  Both are
        :func:`~app.services.balance_at.cash_outstanding_difference`'s own
        answer; this function adds no scope rule of its own.

    Raises:
        BaselineMissingError: When *ctx* carries no baseline scenario.
        ForeignAccountError: When *account* belongs to another owner.
        PayCalendarError: When the owner's paydays cannot define a calendar,
            and :exc:`RuntimeError` when a planned row names a pay period that
            calendar does not hold.  **Neither is this function's own**: both
            come out of :func:`~._cash_fold.assembled_fold`, which is the door
            it takes the walk through, and they are listed because a caller
            reading only this signature would not know it assembles a whole
            fold to read three fields off it.

    **The comparison is only loaded where there is a figure to place it
    beside**, which is not an optimisation of a rare path: eight of the
    developer's nine accounts are a kind this question does not apply to, and
    :func:`~app.services.bank_agreement.bank_agreement` draws up to 731 days.

    **What it costs, measured on the dev database 2026-09-01 rather than
    estimated.**  On a pass whose cash fold is already assembled -- which is
    the cash detail page's situation, since the band builder folds the account
    before this card is built -- the whole instrument is **6 SQL statements**,
    every one of them the bank comparison's; the FIGURE itself is **0**,
    because it reads the fold the pass already holds.  On a COLD pass it is 25,
    which is the fold's own assembly and not this function's.
    *An earlier draft of this paragraph said five, which was the comparison's
    cost BEFORE this step added ``covered_runs`` to it -- a number measured
    once and then quoted through a change that moved it.*
    """
    difference = balance_at.cash_outstanding_difference(account, ctx)
    if difference is None:
        return None
    agreement = bank_agreement.bank_agreement(account, ctx)
    return OutstandingDifference(
        difference=difference,
        reconciliation=(
            None if agreement is None
            else _over_span(agreement, difference.span)
        ),
    )


def _over_span(
    agreement: "bank_agreement.BankAgreement",
    span: "balance_at.BooksSpan",
) -> SpanAgreement:
    """Fold *agreement*'s days down to the verdict for one *span*.

    **Days are counted, never re-derived.**  Membership comes from
    :attr:`~app.services.bank_agreement.AgreementDay.in_records` and
    disagreement from
    :attr:`~app.services.bank_agreement.AgreementDay.agrees`, both of which are
    the report's own published rules; nothing here re-tests a residue, re-reads
    a line or issues a query.  So the verdict and the comparison page's own
    per-day table are two readings of ONE comparison rather than two
    implementations of "does the bank agree".

    Args:
        agreement: The account's comparison
            (:func:`~app.services.bank_agreement.bank_agreement`).
        span: The days the outstanding difference accumulated over
            (:attr:`~app.services.balance_at.CashOutstandingDifference.span`).
            An INVERTED span is legal and answers with a zero
            :attr:`~SpanAgreement.day_count`, which
            :attr:`~SpanAgreement.reconciles` reads as *nothing was checked*.

    Returns:
        The :class:`SpanAgreement`.
    """
    inside = [
        day for day in agreement.days
        if span.first_day <= day.day <= span.last_day
    ]
    compared = [day for day in inside if day.in_records]
    return SpanAgreement(
        first_day=span.first_day,
        last_day=span.last_day,
        # From the span's OWN ends rather than from the report's days: a day
        # the report never drew is a day nothing checked, and counting only
        # what was drawn would report a truncated comparison as a whole one.
        day_count=_days_between(span.first_day, span.last_day),
        compared=len(compared),
        disagreeing=len([day for day in compared if not day.agrees]),
        imported=sum(
            _days_between(max(start, span.first_day), min(end, span.last_day))
            for start, end in agreement.imports
        ),
    )


def _days_between(first_day: date, last_day: date) -> int:
    """Return how many civil days an inclusive range holds.

    **Arithmetic rather than a materialised list, and the reason is a real
    hazard rather than tidiness.**  The span this counts starts at an
    account's ``opened_on + 1``, and ``opened_on`` is USER-SUPPLIED through the
    books-restatement form with no lower bound -- ``opening_service`` refuses a
    future day, a day at or after a movement, a matched line or an assertion,
    and nothing refuses 1900.  Building the day list would allocate ~46,000
    ``date`` objects on every cash-detail render and every ``balanceChanged``
    refresh of the card, which is the same class of defect
    ``bank_agreement._MAX_COMPARED_DAYS`` was added for after a two-line
    statement rendered 26 MB.  Found by adversarial review 2026-09-01.

    Args:
        first_day: The range's first day, inclusive.
        last_day: The range's last day, inclusive.

    Returns:
        The day count, or ``0`` for an INVERTED range -- which is what an empty
        :class:`~app.services.balance_at.BooksSpan` is, and what the caller
        above relies on when it intersects a span with an import's run that
        does not overlap it at all.
    """
    if last_day < first_day:
        return 0
    return (last_day - first_day).days + 1
