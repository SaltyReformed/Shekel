"""
Shekel Budget App -- Reference Table Enums

Python Enums whose members correspond 1:1 with rows in the ref schema
lookup tables.  The *value* of each member is the database ``name``
column after all migrations have run.

These enums are the single source of truth for valid reference values.
The ref_cache module maps each member to its database integer ID at
startup, so application code never needs to query by name at runtime.
"""

import enum


class StatusEnum(enum.Enum):
    """Transaction status values.

    Values match ``ref.statuses.name`` after the Commit #1 migration
    renames the display names.
    """

    PROJECTED = "Projected"
    DONE = "Paid"          # Renamed from "done" -- expense has been paid
    RECEIVED = "Received"  # Income has been deposited
    CREDIT = "Credit"      # Paid via credit card, not checking
    CANCELLED = "Cancelled"
    SETTLED = "Settled"    # Archived / fully reconciled


class TxnTypeEnum(enum.Enum):
    """Transaction type values.

    Values match ``ref.transaction_types.name`` after the Commit #2
    migration capitalizes the display names.
    """

    INCOME = "Income"
    EXPENSE = "Expense"


class AcctCategoryEnum(enum.Enum):
    """Account type category values.

    Groups account types into high-level buckets for dashboard layout
    and chart axis assignment.  Values match
    ``ref.account_type_categories.name``.
    """

    ASSET = "Asset"
    LIABILITY = "Liability"
    RETIREMENT = "Retirement"
    INVESTMENT = "Investment"


class AcctTypeEnum(enum.Enum):
    """Account type values.

    Values match ``ref.account_types.name`` after the Commit #2
    migration capitalizes the display names.  Each member maps 1:1
    to a row in the account_types table.
    """

    CHECKING = "Checking"
    SAVINGS = "Savings"
    HYSA = "HYSA"
    MONEY_MARKET = "Money Market"
    CD = "CD"
    HSA = "HSA"
    CREDIT_CARD = "Credit Card"
    MORTGAGE = "Mortgage"
    AUTO_LOAN = "Auto Loan"
    STUDENT_LOAN = "Student Loan"
    PERSONAL_LOAN = "Personal Loan"
    HELOC = "HELOC"
    K401 = "401(k)"
    ROTH_401K = "Roth 401(k)"
    TRADITIONAL_IRA = "Traditional IRA"
    ROTH_IRA = "Roth IRA"
    BROKERAGE = "Brokerage"
    PLAN_529 = "529 Plan"
    PROPERTY = "Property"


class DeductionTimingEnum(enum.Enum):
    """Deduction timing values.

    Values match ``ref.deduction_timings.name`` in the database.
    """

    PRE_TAX = "pre_tax"
    POST_TAX = "post_tax"


class CalcMethodEnum(enum.Enum):
    """Calculation method values.

    Values match ``ref.calc_methods.name`` in the database.
    """

    FLAT = "flat"
    PERCENTAGE = "percentage"


class TaxTypeEnum(enum.Enum):
    """Tax type values.

    Values match ``ref.tax_types.name`` in the database.
    """

    FLAT = "flat"
    NONE = "none"
    BRACKET = "bracket"


class RaiseTypeEnum(enum.Enum):
    """Salary-raise type values.

    Selects how a scheduled salary raise is treated by the retirement
    salary projection's merit horizon (Gate A ruling 3 / fork F4): a
    ``cola`` recurring raise extrapolates all the way to the retirement
    date (nominal-frame consistency), while ``merit`` and ``custom``
    raises apply only through the merit-horizon cutoff and then stop
    (their earned effect persists in the base).  The 2-year paycheck
    pipeline never consults this discrimination -- it applies every raise
    uniformly via ``paycheck_calculator.apply_raises``.  Values match
    ``ref.raise_types.name``; resolved to IDs via
    ``ref_cache.raise_type_id`` and compared by ID, never by name.
    """

    MERIT = "merit"
    COLA = "cola"
    CUSTOM = "custom"


class RecurrenceUnitEnum(enum.Enum):
    """Recurrence cadence-unit values (recurrence redesign, step R2).

    The first of the two axes that replaced the closed pattern set's
    eight-name enum, ``RecurrencePatternEnum``, deleted with
    ``ref.recurrence_patterns`` at plan step **R9**: a recurrence is
    ``every <interval_n> <unit>``.
    Four of the old names -- Monthly, Quarterly, Semi-Annual, Annual -- were
    the same idea with a different integer baked into the NAME (every 1, 3, 6
    or 12 months), which is why "every other month" and "every two years" had
    nowhere to live.  With the interval in a column, they do.

        period -- the user's own pay-period cadence (the paycheck-space
                  family: today's Every Period / Every N Periods).
        week   -- calendar weeks; weekly and biweekly-by-date bills, which
                  the old set could not express at all.
        month  -- calendar months, month-end clamped (``interval_n`` 1 =
                  Monthly, 3 = Quarterly, 6 = Semi-Annual).
        year   -- calendar years (``interval_n`` 1 = Annual).

    Values match ``ref.recurrence_units.name``; application code resolves
    them via ``ref_cache.recurrence_unit_id`` and compares the integer ID,
    never the string ``name`` -- the project-wide IDs-for-logic invariant.
    """

    PERIOD = "period"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class PeriodPlacementEnum(enum.Enum):
    """How an occurrence date maps onto a pay period (redesign step R2).

    An occurrence is a calendar DATE; a Shekel row lives in a pay PERIOD.
    This is the rule that carries one to the other, and it is a real user
    choice rather than a derived detail: it is the axis today's Monthly and
    Monthly First patterns differ on.  (They differ on the anchor day too --
    Monthly anchors on its ``day_of_month``, Monthly First on the 1st -- so
    placement alone does not carry one to the other: Monthly on the 15th with
    the placement swapped is "the first paycheck starting on or after the
    15th", i.e. the SECOND of the month, not the first.)

        containing_date            -- the period whose date range contains
                                      the occurrence (today's Monthly,
                                      Quarterly, Semi-Annual, Annual).
        period_starting_on_or_after -- the first period whose start_date is
                                      on or after the occurrence (today's
                                      Monthly First: the first paycheck of
                                      each month).

    Well-defined for every unit rather than inert for some: under the
    ``period`` unit an occurrence date IS a period start, so both values
    resolve to the same period.

    Values match ``ref.period_placements.name``; resolved via
    ``ref_cache.period_placement_id`` and compared by integer ID.
    """

    CONTAINING_DATE = "containing_date"
    PERIOD_STARTING_ON_OR_AFTER = "period_starting_on_or_after"


class BusinessDayShiftEnum(enum.Enum):
    """Weekend/holiday adjustment for an occurrence date (redesign step R2).

    Seeded by step R2 with every rule set to ``none``; step R8 is the first
    step that lets a user choose another value, so the column turns
    behaviour ON rather than being added later.

        none  -- take the occurrence date as computed.
        prior -- move a non-business-day occurrence BACKWARD to the previous
                 business day (the usual choice for a bill: pay early rather
                 than late).
        next  -- move it FORWARD to the following business day.

    The shift applies to the CASH date only.  A bill contractually due Aug 1
    and paid on Friday July 30 because Aug 1 is a Sunday still satisfies the
    Aug 1 installment, so the due-date side of a rule is never shifted.

    Values match ``ref.business_day_shifts.name``; resolved via
    ``ref_cache.business_day_shift_id`` and compared by integer ID.
    """

    NONE = "none"
    PRIOR = "prior"
    NEXT = "next"


class GoalModeEnum(enum.Enum):
    """Savings goal amount mode values.

    Values match ``ref.goal_modes.name`` in the database.
    """

    FIXED = "Fixed"
    INCOME_RELATIVE = "Income-Relative"


class IncomeUnitEnum(enum.Enum):
    """Income multiplier unit values.

    Values match ``ref.income_units.name`` in the database.
    """

    PAYCHECKS = "Paychecks"
    MONTHS = "Months"


class RoleEnum(enum.Enum):
    """User role values.

    Values match ``ref.user_roles.name`` in the database.
    """

    OWNER = "owner"
    COMPANION = "companion"


class LoanAnchorSourceEnum(enum.Enum):
    """Loan anchor event source values (CRIT-02 / E-18).

    Distinguishes the origination event that every loan carries from
    user-initiated balance true-ups appended through the dashboard
    edit flow, and from the tracking-start assertion a mid-life-imported
    loan records so its ledger has a recent verified balance to reset
    at.  Values match ``ref.loan_anchor_sources.name``.
    """

    ORIGINATION = "origination"
    USER_TRUEUP = "user_trueup"
    # A mid-life loan's first tracked balance: the operator started
    # tracking an already-amortizing loan and recorded its real balance
    # as of a date at/before the first recorded payment.  It is an
    # ordinary balance ASSERTION (is_opening=False) that RESETS the
    # running balance at its own date, exactly like a user true-up.
    # It does NOT open the ledger: since plan step C1 the loan's ONE
    # opening is ALWAYS the synthesized ORIGINATION, because opening at
    # a mid-life tracking start read the loan out of existence for its
    # whole pre-tracking window (finding B-11, a false $0.00 that made
    # the year-end summary report negative principal paid on real data).
    # See ``app.services.loan_loaders.load_loan_anchor_facts``.
    TRACKING_START = "tracking_start"


class EmployerContributionTypeEnum(enum.Enum):
    """Employer retirement-contribution type values (#38).

    Selects the employer-contribution formula the growth engine
    applies to an investment/retirement account: no employer
    contribution, a flat percentage of gross pay, or a match of the
    employee's contribution up to a cap.  Values match
    ``ref.employer_contribution_types.name``.
    """

    NONE = "none"
    FLAT_PERCENTAGE = "flat_percentage"
    MATCH = "match"


class CompoundingFrequencyEnum(enum.Enum):
    """Interest compounding frequency values (#38).

    Selects the per-period compounding formula the interest
    projection engine applies to an interest-bearing account.  Values
    match ``ref.compounding_frequencies.name``.
    """

    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class LedgerAccountClassEnum(enum.Enum):
    """Ledger account class values for the double-entry posting ledger.

    The five fundamental accounting classes (Build-Order Step 2) plus the
    reporting class ruling **R-FO** added (plan step X-f3d).  Values match
    ``ref.ledger_account_classes.name``.  Asset and Expense are debit-normal;
    Liability, Income, Equity and Unrealized are credit-normal -- the
    natural-balance side is stored as the ``is_debit_normal`` boolean on
    each row and read via ``ref_cache.ledger_class_is_debit_normal``,
    never inferred from these member names.

    ``UNREALIZED`` is OTHER COMPREHENSIVE INCOME: a price movement the owner
    has not sold into cash.  It is a class of its own rather than more Income
    because ``net_income = income - expense``, so a ``$40,000`` house
    revaluation booked as Income would read as ``$40,000`` earned.  The income
    statement reports it BELOW the net-income line and the balance sheet folds
    it into Equity as one derived accumulated line, exactly as Income and
    Expense are folded into Retained Earnings -- so the trial balance closes
    with it, not despite it.  Its accounts are per-account
    (``LedgerAccountKindEnum.UNREALIZED_CHANGE``) and are booked by an
    ``INVESTMENT`` / ``APPRECIATING`` account's balance-assertion true-up.
    """

    ASSET = "Asset"
    LIABILITY = "Liability"
    INCOME = "Income"
    EXPENSE = "Expense"
    EQUITY = "Equity"
    # ``String(12)`` on the ref column, which is why the member value is the
    # bare adjective rather than "Change in Value": the class NAME is an
    # identifier (matched to the enum by ``ref_cache.init``), and the reader's
    # section headings carry the prose.
    UNREALIZED = "Unrealized"


class PostingKindEnum(enum.Enum):
    """Posting-leg kind values for ``budget.account_postings``.

    ``transfer`` is a transfer's two balanced legs (Build-Order Step 2);
    ``income`` / ``expense`` are an ordinary settled transaction's cash and
    category legs (Build-Order Step 3); ``principal`` / ``interest`` /
    ``escrow`` / ``refund`` are the four legs of a confirmed loan payment's
    real-split correction (Build-Order Step 4) -- the loan principal
    adjustment, the accrued interest expense, the configured escrow expense,
    and the payoff-overpayment refund receivable.  ``opening`` / ``trueup``
    are the two-leg kinds the loan read switch adds (Build-Order Step 4,
    second half): ``opening`` books a loan's origination balance once as a
    balanced opening-equity entry so the ledger is authoritative for the
    confirmed balance, and ``trueup`` books an append-only dated correction
    that drives the ledger balance to a user-verified value without rewriting
    the prior payment postings.  Later Build-Order steps add further kinds
    via data migrations.  Values match ``ref.posting_kinds.name``.
    """

    TRANSFER = "transfer"
    INCOME = "income"
    EXPENSE = "expense"
    PRINCIPAL = "principal"
    INTEREST = "interest"
    ESCROW = "escrow"
    REFUND = "refund"
    OPENING = "opening"
    TRUEUP = "trueup"


class PostingSourceEnum(enum.Enum):
    """Journal-entry source-event values for ``budget.journal_entries``.

    ``transfer`` is a settled transfer (Build-Order Step 2); ``transaction``
    is an ordinary settled cash transaction (Build-Order Step 3);
    ``loan_payment`` is the real-split correction appended to a confirmed
    loan-payment transfer (Build-Order Step 4).  ``loan_opening`` /
    ``loan_trueup`` are the source events the loan read switch adds
    (Build-Order Step 4, second half): ``loan_opening`` tags the once-per-loan
    opening-equity entry booked at origination, and ``loan_trueup`` tags an
    append-only loan-balance correction entry.  ``account_opening`` /
    ``account_trueup`` are their NON-loan siblings (Build-Order Step 5):
    ``account_opening`` tags the once-per-account entry booking a non-loan
    account's earliest anchor assertion, and ``account_trueup`` tags the
    balanced correction appended for each later anchor true-up -- together
    they make every non-loan linked ledger sum to an ABSOLUTE balance, closing
    the app-wide trial balance.  None of the four links a transfer or a
    transaction (both source FKs are nullable); the source kind is what
    disambiguates them.  ``purchase`` is a single PURCHASE recorded against an
    envelope whose bank posting day the owner has recorded (plan step X-f3b,
    ruling **R-FM**): it links ``transaction_entry_id`` and carries NULL in the
    other two source FKs, and it books its own cash leg on its own day so its
    envelope's close books only the remainder.  Later steps add ``paycheck``
    and ``credit_payback`` via data migrations.  Values match
    ``ref.posting_sources.name``.
    """

    TRANSFER = "transfer"
    TRANSACTION = "transaction"
    PURCHASE = "purchase"
    LOAN_PAYMENT = "loan_payment"
    LOAN_OPENING = "loan_opening"
    LOAN_TRUEUP = "loan_trueup"
    ACCOUNT_OPENING = "account_opening"
    ACCOUNT_TRUEUP = "account_trueup"


class LedgerAccountKindEnum(enum.Enum):
    """Row-kind discriminator for ``budget.ledger_accounts`` (Build-Order Step 4).

    The explicit, positive discriminator that replaces inferring a ledger
    account's kind from the NULL-pattern of its ``account_id`` /
    ``category_id`` / ``is_fallback`` columns (see
    :class:`app.models.ledger_account.LedgerAccount`).  Every row carries a
    ``kind_id`` FK to one of these values; readers branch on the integer ID,
    never on which FKs happen to be NULL.

    The first four enumerate the kinds Steps 2-3 already create:

        linked    -- one per real ``budget.accounts`` row (Asset/Liability).
        category  -- one per budget category per Income/Expense class.
        fallback  -- the per-(owner, class) Uncategorized bucket.
        orphan    -- a former category row whose category was deleted.

    The next three are the per-loan ledger accounts Step 4's loan-payment
    correction books into:

        loan_interest -- the loan's accrued-interest Expense account.
        loan_escrow   -- the loan's configured-escrow Expense account.
        loan_refund   -- the loan's payoff-overpayment refund Asset account.

    The loan read switch (Build-Order Step 4, second half) adds one more
    per-loan account:

        equity_opening -- the loan's opening-balance Equity account, the
                          credit counter-leg of the once-per-loan
                          opening-equity entry that books the origination
                          balance so the ledger is authoritative for the
                          loan's confirmed balance.

    Build-Order Step 5 adds the non-loan sibling:

        anchor_equity -- a non-loan account's opening/true-up Equity account
                         (``account_id`` set, like ``linked``), the counter-leg
                         of its ``account_opening`` / ``account_trueup``
                         corrections.  One per real account, coexisting with
                         the linked row under the ``(account_id, kind_id)``
                         partial unique that Step 5's chart migration re-keys
                         ``uq_ledger_accounts_account`` into.

    Ruling **R-FO** (plan step X-f3d) adds the two kinds that say what a
    TRUE-UP's difference WAS, dispatched over the account's projection kind:

        interest_income  -- an ``INTEREST`` account's per-account Interest
                            Income account (Income class).
        unrealized_change  -- an ``INVESTMENT`` or ``APPRECIATING`` account's
                            per-account Change in Value account
                            (``LedgerAccountClassEnum.UNREALIZED``).

    Both carry ``account_id`` and share the ``anchor_equity`` shape and its
    ``(account_id, kind_id)`` unique exactly, so no new index exists for them.
    An account's OPENING keeps booking to ``anchor_equity`` whatever its kind:
    an opening is capital brought onto the books, not something earned (a
    Property's ``$350,000.00`` opening is not a gain).

    Application code resolves these via ``ref_cache.ledger_account_kind_id``
    and compares against the integer ID -- never the string ``name`` --
    matching the project-wide ``ref-table: IDs for logic, strings for display
    only`` invariant.
    """

    LINKED = "linked"
    CATEGORY = "category"
    FALLBACK = "fallback"
    ORPHAN = "orphan"
    LOAN_INTEREST = "loan_interest"
    LOAN_ESCROW = "loan_escrow"
    LOAN_REFUND = "loan_refund"
    EQUITY_OPENING = "equity_opening"
    ANCHOR_EQUITY = "anchor_equity"
    INTEREST_INCOME = "interest_income"
    UNREALIZED_CHANGE = "unrealized_change"


class AmountSourceEnum(enum.Enum):
    """WHICH RELATION states a row's amount, when the row does not state it itself.

    Ruling **R-FI**'s discriminator (plan step **X-au-c1**): a row's amount is
    either its OWN -- a human authored the figure, or the money moved -- or it
    is DERIVED, and a derived amount is not stored at all.  A row that owns its
    amount carries ``amount_source_id IS NULL`` and a figure; a derived row
    names one of these and carries no figure.  The pairing is the CHECK
    ``ck_transactions_amount_ownership`` / ``ck_transfers_amount_ownership``, so
    a stale derived figure is unrepresentable rather than merely unlikely.

        template        -- the recurring DEFINITION that generated this row
                           states its price: ``Transaction.template_id`` ->
                           ``budget.transaction_templates``, or
                           ``Transfer.transfer_template_id`` ->
                           ``budget.transfer_templates``.
        parent_transfer -- this row is a transfer SHADOW and is worth exactly
                           what its parent transfer is (``transfer_id``), which
                           is Transfer Invariant 3 read rather than maintained.

    **These name the RELATION that prices the row, not the RULE that computes
    the figure, and the difference is a developer ruling (2026-08-12) amending
    R-FI's own enumeration.**  R-FI listed five values naming the five
    producers -- own / salary / template / loan payment / transfer -- and two of
    those are refinements a DEFINITION carries, not facts about a row: a
    template is salary-linked when an active
    :class:`~app.models.salary_profile.SalaryProfile` names it
    (``template_amount_service.is_salary_linked_template``), and a transfer
    template is a loan payment when it holds a
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row
    (``loan_payment_service.loan_payment_config``).  Storing the refinement on
    every generated row copies a definition-level fact onto each of its
    instances, and two LIVE routes then falsify the copy:

    * ``routes/loan/payment_transfer.py`` ``track_payment`` -- the loan
      dashboard's one-click "auto-track the contract" sets
      ``derive_from_loan = True``.  A legacy manual payment has no settings row,
      so its shadows would have been stamped with the plain-transfer rule; after
      the flip the definition's price series is dormant
      (``template_amount_service.owns_its_amount`` is False) and every stamped
      shadow becomes unpriceable.
    * ``routes/salary/profiles.py`` ``delete_profile`` -- archiving a profile is
      the moment a template stops being salary-linked and starts owning its own
      amount, which X-au-a already handles by stating a price through the write
      door in the same unit of work (58 production rows).  Rows stamped with the
      salary rule would keep naming a producer that no longer answers.

    Both are repairable only by a writer that rewrites every affected row -- a
    second maintainer of a derived value, which is the shape this arc exists to
    delete.  Naming the relation instead leaves the refinement where it is a
    fact: the row says *my definition prices me*, and the definition says how.
    ``credit_card:CC4b``'s card payment needs no new value here -- it prices
    through its transfer template's satellite, the same shape as a loan payment
    -- and neither does ``CC5a``'s rewards accrual.  **``CC4c``'s projected
    finance charge DOES**, and a first draft of this paragraph claimed the card
    arc needed none: that row carries no pricing link at all and its producer is
    reached from the row's ACCOUNT, which is finding **N-264**.

    Application code resolves these via ``ref_cache.amount_source_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.  The OWN state is deliberately NOT a member: it is the ABSENCE of
    a source, which is what lets the ownership CHECK be written as
    ``(amount_source_id IS NULL) = (estimated_amount IS NOT NULL)`` -- a
    constraint over two NULL-nesses, with no ref-table id literal frozen into
    the schema.
    """

    TEMPLATE = "template"
    PARENT_TRANSFER = "parent_transfer"


class StatementSourceEnum(enum.Enum):
    """WHERE a recorded statement line came from -- ruling **R-FP**'s adapter.

    A statement importer is a SOURCE ADAPTER over one normalized line shape
    (plan step ``bank_import:X-f6a``), so matching, review and fact-writing stay
    source-independent: a new way for a statement to arrive is a new member here
    and a new parser, never a second path through the importer.

        secu_checking_csv -- State Employees' Credit Union's own transaction
                             export, with the running-balance column included.

    **A member names a FORMAT at an INSTITUTION, not an institution**, because
    one bank publishes one statement several ways and the ways do not carry the
    same facts.  Measured on the developer's own exports 2026-08-16: SECU's OFX
    -- and its QFX and QBO twins, which are the same statement content plus two
    Intuit routing tags -- carries a ``FITID`` and truncates the description to
    the OFX ``NAME`` limit, 326 of 361 lines landing at exactly 32 characters;
    the CSV carries the merchant, the bank's own category and a per-line running
    balance, and carries no ``FITID`` at all.  The CSV description STARTS WITH
    the OFX name on 306 of 306 shared lines, so the two really are one statement
    with one of them cut short.

    **Losing ``FITID`` costs nothing, and that is measured rather than
    assumed.**  A line's identity is
    ``(account, posted_on, amount, sequence within that group)``
    (:func:`app.services.statement_import.line_identity`), which every source
    can compute including one carrying no id of its own.  Compared across two
    SECU exports twelve days apart, that key reproduced the ``FITID`` key
    exactly: 0 keys present in only one export, 0 lines whose ``FITID``
    disagreed, over 342 shared lines.  So an external id is stored as a
    CORROBORATING fact (``bank_statement_lines.external_id``) rather than as the
    thing identity rests on, and the importer has ONE identity rule instead of
    one per format -- which is what lets ``X-f6b``'s automated source join
    without re-opening the question.

    Application code resolves these via ``ref_cache.statement_source_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.
    """

    SECU_CHECKING_CSV = "secu_checking_csv"
