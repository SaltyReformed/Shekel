"""The app's books beside the BANK's own record, day by day.

Plan step ``bank_import:X-f6e-2``, ruling **R-GF**: the comparison is an
INSTRUMENT and never a gate.  A refusal here deadlocks -- the import is what
fixes a disagreement, and a first import has matched nothing -- so this module
reports and refuses nothing.

**Why it compares MOVEMENT and not only level.**  A balance difference alone
cannot see a defect the owner's own true-up has already absorbed, and that is
measured rather than argued: on the developer's Checking account, over the 145
days his statement and his records both cover, **35 days carry a real
disagreement between his rows and the bank's lines, and 11 of them read as
EXACT agreement in the balance difference** because a same-day balance
assertion cancels the error to the cent.  Among the eleven are the ``$943.41``
of card paybacks finding **N-337** names, ``$2,090.47`` on 2026-07-31, and both
``$0.05`` payroll residues finding **N-239** measures.  So each day carries
three facts side by side rather than one:

* what the BANK's lines moved that day -- an observation;
* what the app's OWN settled rows moved that day -- the app's claim;
* what a balance ASSERTION moved that day -- the owner correcting the app.

The first two differ by the RESIDUE, which is the whole instrument: money one
side records and the other does not.  The third is why the running balances can
agree while the residue does not, and naming it is what stops the report
calling a true-up an agreement.

**And the level is still reported**, because the residue cannot say how far
apart the two records are TODAY and cannot say which side rests on an
assumption.  A first import seats its anchor by ``assumed_last_day`` (finding
**N-342**), and the signature of a wrong one is a CONSTANT offset across the
whole span rather than a difference that varies by day -- so the summary says
whether the offset is constant, which is the one shape that convicts the
anchor instead of the books.

**Days before the app's records begin are LABELLED, not counted.**  The
developer's statement starts 2026-01-02 and his records start 2026-03-26, so
83 days show real bank movement against an app that holds nothing -- which is
finding **N-314**, owned by ``balance:X-f3c``, and not a disagreement this
report can act on.  They are shown, because hiding a day the bank describes
would be the report deciding what the owner may see, and excluded from every
total and count, because a figure this arc cannot act on must not be added to
one it can.

Services-boundary discipline: no Flask import, no clock read -- the reader's
NOW arrives on the :class:`~app.services.balance_at.BalanceContext`.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatchMember
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import balance_at, cash_ledger, statement_import
from app.utils.dates import days_in_range

_ZERO_MONEY = Decimal("0.00")
_ONE_DAY = timedelta(days=1)

#: The most days this report will DRAW, counted back from the most recent.
#:
#: **A bound rather than a preference, because the span is user-supplied
#: and nothing else bounds it.** ``bank_statement_lines.posted_on`` carries
#: no CHECK and the SECU adapter parses any four-digit year, so ONE line
#: dated 1900 -- a bank placeholder row, a mis-parsed column, a typo --
#: makes the recorded span 46,000 days wide. Measured by adversarial
#: review 2026-08-24: two lines, one dated 1900-01-01 and one dated today,
#: rendered **26,402,650 bytes over 46,257 rows**, and every later load of
#: that page allocates it again with no way to get the page back short of
#: deleting the import. Two years covers any statement history this app
#: has seen -- the developer's own is 232 days -- and the page SAYS when
#: it has drawn fewer days than are recorded, which is the same discipline
#: :attr:`BankAgreement.recorded_through` applies at the other end.
_MAX_COMPARED_DAYS = 731

#: How many compared days a CONSTANT offset needs before it means
#: anything.  One day satisfies "every gap is equal" vacuously.
_MIN_DAYS_FOR_A_CONSTANT = 2


@dataclass(frozen=True)
class AgreementDay:
    """One day of the two records, side by side.

    Attributes:
        day: The civil day.
        bank_lines: What the bank's recorded lines moved -- signed, positive
            money IN, the convention
            :class:`~app.models.statement_import.BankStatementLine` states once
            and the app's own cash leg already shares.
        recorded: What the app's own SETTLED rows moved that day.
        asserted: What a balance assertion moved that day.  The account's
            OPENING assertion contributes nothing here: it establishes the
            level rather than moving money on its day (ruling R-I).
        app_balance: What the app says the account held at the day's end.
        bank_balance: What the bank's own record says it held, or ``None``
            when no anchored import places a figure, or when the recorded
            lines do not reach this day from the one that does.
        in_records: Whether the app's records reach back this far.  ``False``
            for a day before the account's first cash fact, where a zero
            :attr:`recorded` means "nothing recorded" rather than "nothing
            happened".
    """

    day: date
    bank_lines: Decimal
    recorded: Decimal
    asserted: Decimal
    app_balance: Decimal
    bank_balance: "Decimal | None"
    in_records: bool

    @property
    def residue(self) -> Decimal:
        """Return what the app records that the bank does not, signed.

        **The instrument.**  Positive means the app's own rows moved MORE into
        the account than the bank's lines did that day; negative means the
        bank moved more.  Either way it is money exactly one of the two
        records has, which is the question a reconciliation asks.
        """
        return self.recorded - self.bank_lines

    @property
    def gap(self) -> "Decimal | None":
        """Return how far the two running balances stand apart, or ``None``.

        ``None`` exactly when :attr:`bank_balance` is -- there is no second
        figure to stand apart from.
        """
        if self.bank_balance is None:
            return None
        return self.app_balance - self.bank_balance

    @property
    def agrees(self) -> bool:
        """Return whether this day's two records say the same thing.

        **Tested on the RESIDUE, never on the gap.**  A gap of zero over a
        non-zero residue is the exact shape this report exists to stop reading
        as agreement: the owner's own true-up cancelled the error that day, and
        eleven of the developer's thirty-five real disagreements look like
        that.
        """
        return self.residue == _ZERO_MONEY


@dataclass(frozen=True)
class ComparedSpan:
    """Which days were DRAWN, against which days are RECORDED.

    **One value rather than four dates on the report**, because the pair of
    pairs is one subject: a reader has to know both what was compared and what
    exists, or a report of PART of the record reads as a report of the record.

    Attributes:
        first_day: The earliest day DRAWN.
        last_day: The latest day drawn -- never past the reader's NOW, so no
            still-Projected row can land inside the range (ruling R-G clamps
            one to ``as_of + 1``).  **It precedes :attr:`first_day` exactly
            when the whole statement postdates that NOW**: nothing was
            comparable, which is a different fact from holding no statement.
        recorded_from: The FIRST day the account has a recorded line for.
        recorded_through: The last day it has one for.
    """

    first_day: date
    last_day: date
    recorded_from: date
    recorded_through: date

    @property
    def starts_late(self) -> bool:
        """Return whether recorded days fall BEFORE the drawn range.

        True when the recorded span reaches further back than this report
        draws (:data:`_MAX_COMPARED_DAYS`).  On real data that means a line is
        mis-dated, and the page SAYS so rather than rendering the span: two
        lines, one dated 1900-01-01 and one dated today, rendered 46,257 rows
        and 26 MB before the bound existed.
        """
        return self.recorded_from < self.first_day

    @property
    def ends_early(self) -> bool:
        """Return whether recorded days fall AFTER the drawn range.

        True when lines exist past the reader's NOW.  **Reported so the
        truncation is never silent**: understating a comparison is the one
        failure this page cannot afford.
        """
        return self.recorded_through > self.last_day


@dataclass(frozen=True)
class BankAgreement:
    """One account's books beside its bank's record, over the recorded span.

    Attributes:
        account_id: The account compared.
        span: The :class:`ComparedSpan` -- what was drawn, against what exists.
        records_begin: The day the app's own records start, or ``None`` for an
            account holding none.  Days before it are :attr:`AgreementDay`
            values with ``in_records`` False.
        anchor: The :class:`~app.services.statement_import.BankAnchor` every
            bank balance here was walked from, or ``None`` when no import
            places a figure -- in which case the LEVEL is unknown and only the
            movement half of this report is answerable.
        days: One :class:`AgreementDay` per day of the span, ascending.
    """

    account_id: int
    span: ComparedSpan
    records_begin: "date | None"
    anchor: "statement_import.BankAnchor | None"
    days: "list[AgreementDay]"

    @property
    def compared(self) -> "list[AgreementDay]":
        """Return only the days the app's records reach.

        The days every total below is taken over.  A day the app has no
        records for is not a disagreement, so counting one would report
        finding **N-314** as this arc's defect 83 times.
        """
        return [day for day in self.days if day.in_records]

    @property
    def disagreeing(self) -> "list[AgreementDay]":
        """Return the compared days whose two records differ."""
        return [day for day in self.compared if not day.agrees]

    @property
    def app_ahead(self) -> Decimal:
        """Return the total by which the app's own rows ran AHEAD of the bank.

        **It says which balance ends higher, never whose record holds the
        missing item**, and the distinction is the correction of the obvious
        wrong caption.  A positive residue has TWO causes -- income the app
        recorded and the bank did not, and SPENDING the bank recorded and the
        app did not -- and they sit on opposite sides of the books.  Naming
        this "money you show and your bank does not" would be false of the
        second, which is the commoner one: a bank debit nobody entered.

        Signed positive, matching :attr:`AgreementDay.residue`'s own
        convention, so this and :attr:`bank_ahead` sum to the net.
        """
        return sum(
            (day.residue for day in self.disagreeing if day.residue > 0),
            _ZERO_MONEY,
        )

    @property
    def bank_ahead(self) -> Decimal:
        """Return the total by which the BANK's lines ran ahead of the app's rows.

        The mirror of :attr:`app_ahead`, and with the same two causes reversed:
        a deposit the bank posted that the app never recorded, and spending the
        app recorded that the bank never posted.

        **A MAGNITUDE, positive**, where the residues behind it are negative.
        It was signed so the two totals summed to the net, and that put
        ``-$15,028.03`` on screen under the words "ran ahead" -- a magnitude
        caption over a signed figure, found by two independent adversarial
        reviews 2026-08-24.  The net is :attr:`app_ahead` MINUS this.
        """
        return -sum(
            (day.residue for day in self.disagreeing if day.residue < 0),
            _ZERO_MONEY,
        )

    @property
    def asserted_total(self) -> Decimal:
        """Return what balance assertions moved across the compared days.

        Reported beside the residues because it is the app correcting itself,
        never a movement the bank should show -- and because its size against
        the residues says how much work the true-ups are doing to keep a
        drifting book level.

        **Signed, and a signed sum UNDERSTATES that work**, which is worth
        stating rather than hiding: two ``$5,000`` corrections in opposite
        directions read as ``$0``.  :attr:`asserted_gross` is the same days
        summed by magnitude, and the two together are the honest pair.  An
        earlier draft quoted a figure here that no window produced -- it was the
        sum taken BEFORE the opening assertion was excluded, ``$798.03`` stale,
        found by adversarial review 2026-08-24.  Measurements now live in the
        step's own plan entry, where they are re-derived rather than remembered.
        """
        return sum((day.asserted for day in self.compared), _ZERO_MONEY)

    @property
    def asserted_gross(self) -> Decimal:
        """Return what balance assertions moved, summed by MAGNITUDE.

        The companion :attr:`asserted_total` needs: corrections that cancel are
        still corrections, and the gross is what says how often the book had to
        be pulled back into line.
        """
        return sum(
            (abs(day.asserted) for day in self.compared), _ZERO_MONEY,
        )

    @property
    def unpriced_days(self) -> int:
        """Return how many drawn days the bank's own record cannot price.

        A day is unpriced when the recorded lines do not REACH it from the
        anchor -- across a gap between imports, or from a different run
        entirely.  Reported because the page would otherwise say *"the bank's
        balances below are walked from $X on D"* over a column of dashes,
        which is a derivation claimed and not performed.

        Reproduced by adversarial review 2026-08-24: a ``file_chain`` anchor in
        a January run left every August day unpriced -- including the effective
        day of August's OWN anchored import, whose crossing is empty -- while
        the page named the January figure.

        Returns:
            The count, or ``0`` when no anchor places a balance at all (the
            page says THAT instead, and one absence should not be reported as
            two).
        """
        if self.anchor is None:
            return 0
        return sum(1 for day in self.days if day.bank_balance is None)

    @property
    def standing_gap(self) -> "Decimal | None":
        """Return how far the two running balances stand apart at the END.

        The LEVEL, as one number: what the app says the account held on the
        last compared day, less what the bank's own record says.  Reported
        because the residue cannot say it -- a residue is per day, and the
        question "how far apart are my books and my bank right now" is about
        the running totals.

        Returns:
            The last compared day's gap, or ``None`` when there is no compared
            day or no anchor places a bank balance on it.
        """
        compared = self.compared
        return compared[-1].gap if compared else None

    @property
    def constant_offset(self) -> "Decimal | None":
        """Return the offset both records share on EVERY compared day, or ``None``.

        **A constant offset convicts a STARTING FIGURE, not the movements**
        (ruling **R-GF**, finding **N-342**): a level wrong by ``K`` shifts
        every derived balance by ``K`` while leaving every day's movement
        right, and a difference that VARIES by day cannot be that.

        **It does NOT say WHICH starting figure**, and an earlier draft of this
        claimed the anchor -- refuted by adversarial review 2026-08-24.  There
        are two levels in play: the bank's anchor, which a first import seats by
        assumption (**N-342**), and the app's own opening ASSERTION.  A constant
        gap is exactly as consistent with one being wrong as the other.

        Returns:
            The shared offset, or ``None``.  ``None`` covers three cases: no
            anchor places a bank balance, the offset MOVES (which says
            something about the records instead), or **too little was compared
            to call anything constant**.

        **"Constant" needs something to be constant ACROSS.** One compared day
        satisfies "every gap is equal" vacuously, and an earlier draft reported
        a single day's offset as the signature -- on exactly the population
        N-342 describes, an owner who imports a statement the same week they
        start recording.  Reproduced by adversarial review 2026-08-24.  Two
        compared days are therefore required.

        **A further guard on "some money actually MOVED" was written and then
        dropped**, because the span's own endpoints are line days: the last
        compared day carries a line in every reachable case, so the guard could
        not be constructed and would have been an untested branch standing for
        a case that does not arise.
        """
        compared = self.compared
        if len(compared) < _MIN_DAYS_FOR_A_CONSTANT:
            return None
        gaps = {day.gap for day in compared}
        if len(gaps) != 1:
            return None
        # A single-element set of ``None`` pops ``None``, which is the answer
        # for an account with no anchor -- so no separate guard says it twice.
        return gaps.pop()


@dataclass(frozen=True)
class DayLine:
    """One bank line on a day, as the drill-down shows it.

    Attributes:
        amount: The signed figure the bank posted.
        description: The bank's own prose.
        merchant: The merchant the adapter read, or ``None``.
        matched: Whether a statement match already claims this line.
    """

    amount: Decimal
    description: str
    merchant: "str | None"
    matched: bool


@dataclass(frozen=True)
class DayRow:
    """One of the app's own settled movements on a day.

    Attributes:
        amount: The signed cash the row moved -- the same
            :attr:`~app.services.cash_ledger.CashSourceFact.delta` the fold
            counted, so the rows shown here sum to the day's ``recorded``.
        description: What the row is called.
        matched: Whether a statement match already claims it.
    """

    amount: Decimal
    description: str
    matched: bool


@dataclass(frozen=True)
class DayDetail:
    """What makes up one day's difference, on both sides.

    Attributes:
        day: The day explained.
        lines: The bank's lines posted that day.
        rows: The app's own settled movements that day.

    **Every line and every row is listed, not only the unmatched ones**, and
    that is the correction of the obvious shortcut.  A row matched to a line
    the bank posted on a DIFFERENT day contributes to both days' residues
    while being unmatched on neither, so a list filtered to "unexplained"
    would omit exactly the timing case a reader is trying to understand.  The
    match flag is shown per item instead, which says the same thing without
    dropping anything.
    """

    day: date
    lines: "list[DayLine]"
    rows: "list[DayRow]"


def bank_agreement(
    account: Account, ctx: balance_at.BalanceContext,
) -> "BankAgreement | None":
    """Return this account's books beside its bank's record, day by day.

    Args:
        account: The account to compare.  Must be session-attached; the caller
            owns the ownership check.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The :class:`BankAgreement`, or ``None`` when the account has no
        recorded statement line AT ALL -- there is no outside record to compare
        against, which is an absence rather than an empty comparison.
        **A statement whose every line postdates the reader's NOW is not that
        case** and answers with an empty :attr:`BankAgreement.days`. Answering
        ``None`` there made the page tell the owner this account held no bank
        lines while the statements page one click away listed them, and nothing
        in the importer refuses a future-dated line -- reproduced by adversarial
        review 2026-08-24.

    Raises:
        BaselineMissingError: When *ctx* carries no scenario.

    **The span ends at the recorded lines' last day or the reader's NOW,
    whichever is earlier**, and the bound is load-bearing rather than
    defensive: a still-Projected row lands at ``as_of + 1`` (ruling R-G), so a
    range stopping at ``as_of`` cannot contain one.  Past that day the app's
    balance carries PLAN, and setting a plan beside a bank line would report
    agreement where the app has merely predicted what the bank did -- or a
    disagreement about money nobody has spent.  When it truncates,
    :attr:`BankAgreement.recorded_through` says so.
    """
    span = statement_import.recorded_span(account.id)
    if span.first_day is None or span.last_day is None:
        return None
    last_day = min(span.last_day, ctx.as_of)
    first_day = max(
        span.first_day, last_day - (_MAX_COMPARED_DAYS - 1) * _ONE_DAY,
    )
    # BOTH ends can leave the drawn range, and an inverted one is legal: a
    # statement entirely in the future yields no comparable day and still
    # reports what it holds.
    days = days_in_range(first_day, last_day)
    series = balance_at.cash_daily_facts_series(
        account, ctx, first_day, last_day,
    )
    bank_moves = _bank_moves(account.id, first_day, last_day)
    folded = statement_import.fold_bank_balances(account.id, days)
    balances = {} if folded is None else folded.balances
    records_begin = series.first_event_on

    return BankAgreement(
        account_id=account.id,
        span=ComparedSpan(
            first_day=first_day,
            last_day=last_day,
            recorded_from=span.first_day,
            recorded_through=span.last_day,
        ),
        records_begin=records_begin,
        anchor=None if folded is None else folded.anchor,
        days=[
            AgreementDay(
                day=day,
                bank_lines=bank_moves.get(day, _ZERO_MONEY),
                recorded=series.facts[day].recorded,
                asserted=series.facts[day].asserted,
                app_balance=series.facts[day].balance,
                bank_balance=balances.get(day),
                in_records=(
                    records_begin is not None and day >= records_begin
                ),
            )
            for day in days
        ],
    )


def day_detail(
    account: Account, ctx: balance_at.BalanceContext, day: date,
) -> DayDetail:
    """Return what makes up one day's difference, on both sides.

    Args:
        account: The account to explain.  Must be session-attached; the caller
            owns the ownership check.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.
        day: The civil day to explain.

    Returns:
        The :class:`DayDetail`.  A day with nothing on either side gets empty
        lists rather than ``None``: "the bank showed nothing and so did you" is
        an answer, and one a reader following a link has asked for.

    Raises:
        BaselineMissingError: When *ctx* carries no scenario.

    **The app's rows come from the same walk the day's ``recorded`` total was
    taken from**, so the items shown sum to the figure they explain.  Listing
    the account's transactions by ``settled_on`` instead would be a second
    statement of which rows count as settled cash and on what day -- the shape
    this codebase has repeatedly paid for.
    """
    return DayDetail(
        day=day,
        lines=[
            DayLine(
                amount=line.amount,
                description=line.description,
                merchant=line.merchant,
                matched=matched,
            )
            for line, matched in _lines_on(account.id, day)
        ],
        rows=[
            DayRow(amount=amount, description=description, matched=matched)
            for amount, description, matched in _rows_on(account, ctx, day)
        ],
    )


def _bank_moves(
    account_id: int, first_day: date, last_day: date,
) -> "dict[date, Decimal]":
    """Return what the bank's recorded lines moved on each day of a range.

    Args:
        account_id: The account whose lines to read.
        first_day: Inclusive first day.
        last_day: Inclusive last day.

    Returns:
        ``{day: signed total}``, one entry per day in the range carrying at
        least one line.

    **The aggregate is the import package's**
    (:func:`~app.services.statement_import.bank_daily_movements`), narrowed to
    the range here rather than restated as a second
    ``SUM(amount) GROUP BY posted_on`` -- which is what this was, and what an
    adversarial review measured on 2026-08-24 as two queries answering one
    question.  The narrowing is in Python because an account holds a few
    hundred recorded days and the same list is what the fold prefix-sums.
    """
    return {
        day: amount
        for day, amount in statement_import.bank_daily_movements(account_id)
        if first_day <= day <= last_day
    }


def _lines_on(
    account_id: int, day: date,
) -> "list[tuple[BankStatementLine, bool]]":
    """Return the bank's lines posted on one day, each with its match state.

    Args:
        account_id: The account whose lines to read.
        day: The posted day.

    Returns:
        ``[(line, matched), ...]`` ordered by recording order (``id``).

    **NOT the bank's own within-day order, and nothing recorded preserves it.**
    An earlier docstring claimed it did.  ``sequence_in_group`` is keyed per
    ``(account, posted_on, amount)`` for line IDENTITY, not statement position,
    and ``id`` is the order lines were first recorded across imports -- so a
    re-import inserting a line the bank had placed ahead of a recorded one puts
    it last here.  Recording order is a defensible, stable choice; the claim
    was the defect.  Found by adversarial review 2026-08-24.
    """
    claimed = {
        row[0]
        for row in db.session.query(
            StatementMatchMember.bank_statement_line_id,
        ).filter(
            StatementMatchMember.account_id == account_id,
            StatementMatchMember.bank_statement_line_id.isnot(None),
        )
    }
    lines = (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.posted_on == day,
        )
        .order_by(BankStatementLine.id)
        .all()
    )
    return [(line, line.id in claimed) for line in lines]


def _rows_on(
    account: Account, ctx: balance_at.BalanceContext, day: date,
) -> "list[tuple[Decimal, str, bool]]":
    """Return the app's own settled movements on one day, with match state.

    Args:
        account: The account whose walk to read.
        ctx: The read pass's context, whose scenario scopes the walk.
        day: The settle day.

    Returns:
        ``[(amount, description, matched), ...]`` largest movement first, so
        the item that explains most of a day's difference reads first.

    **Sourced from the WALK, so these sum to the day's ``recorded`` total.**
    An envelope's purchase is a fact in its own right there
    (:attr:`~app.services.cash_ledger.CashSourceFact.entry_id`), which is why
    the match state is asked of the entry when there is one and of the
    transaction otherwise -- the same two-subject split
    ``statement_match_members`` stores.
    """
    facts = [
        fact
        for fact in cash_ledger.walk_cash_ledger(
            account.id, ctx.amounts().scenario_id,
        ).source_facts
        # A fact that moved NOTHING is not an unexplained row.  An envelope
        # whose whole amount is already booked by its posted purchases carries
        # a ``0.00`` parent leg, and listing it under "no bank line is tied to
        # this row" invites the owner to chase something that cannot be
        # matched and moved no money -- three of them on the developer's real
        # 2026-07-02.  They contribute nothing to the day's total either, so
        # dropping them leaves the sum identity intact.  Found by adversarial
        # review 2026-08-24.
        if fact.settled_on == day and fact.delta != _ZERO_MONEY
    ]
    if not facts:
        return []
    names = _row_names(facts)
    claimed_txns, claimed_entries = _claimed_app_rows(account.id)
    return sorted(
        (
            (
                fact.delta,
                names.get((fact.transaction_id, fact.entry_id), "(unnamed)"),
                fact.entry_id in claimed_entries
                if fact.entry_id is not None
                else fact.transaction_id in claimed_txns,
            )
            for fact in facts
        ),
        key=lambda row: abs(row[0]),
        reverse=True,
    )


def _row_names(facts: list) -> "dict[tuple[int, int | None], str]":
    """Return a display name for each fact's source row.

    Args:
        facts: :class:`~app.services.cash_ledger.CashSourceFact` values.

    Returns:
        ``{(transaction_id, entry_id): name}``.  Two queries at most, and none
        for a day with nothing on it.
    """
    names: "dict[tuple[int, int | None], str]" = {}
    txn_ids = {f.transaction_id for f in facts if f.entry_id is None}
    entry_ids = {f.entry_id for f in facts if f.entry_id is not None}
    if txn_ids:
        for txn_id, name in db.session.query(
            Transaction.id, Transaction.name,
        ).filter(Transaction.id.in_(txn_ids)):
            names[(txn_id, None)] = name
    if entry_ids:
        for entry_id, txn_id, description in db.session.query(
            TransactionEntry.id,
            TransactionEntry.transaction_id,
            TransactionEntry.description,
        ).filter(TransactionEntry.id.in_(entry_ids)):
            names[(txn_id, entry_id)] = description
    return names


def _claimed_app_rows(account_id: int) -> "tuple[set, set]":
    """Return the app rows a statement match already claims.

    Args:
        account_id: The account whose matches to read.

    Returns:
        ``(transaction ids, transaction entry ids)``.
    """
    claimed_txns: set = set()
    claimed_entries: set = set()
    for txn_id, entry_id in db.session.query(
        StatementMatchMember.transaction_id,
        StatementMatchMember.transaction_entry_id,
    ).filter(StatementMatchMember.account_id == account_id):
        if txn_id is not None:
            claimed_txns.add(txn_id)
        if entry_id is not None:
            claimed_entries.add(entry_id)
    return claimed_txns, claimed_entries
