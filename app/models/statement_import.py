"""
Shekel Budget App -- Statement Import Models (budget schema)

What a BANK said, recorded as fact.  Three tables, one subject (plan step
``bank_import:X-f6a``, ruling **R-FP**):

  * :class:`AccountExternalIdentity` -- which account at a SOURCE is which
    account here.  The one-time mapping R-FP calls "a fact, not a guess".
  * :class:`StatementImport` -- one act of importing, and what it did.
  * :class:`BankStatementLine` -- one line the bank showed.

**None of them moves a figure, and that is the leaf boundary rather than a
coincidence.**  Recording what a statement said is separable from deciding
which of the app's own rows it explains, so this leaf lands with no matcher, no
``settled_on`` correction and no clearing link -- the same discipline that made
``X-au-c1`` and ``X-f3a-1`` provably balance-neutral.  What the recorded lines
are FOR is the leaves after it: the match and its review (``X-f6a-2``), the
purchase a bank line becomes (``X-f6a-3b``), the walked-statement silence that
makes an unshown line NOT CLEARED (``balance:X-f3a-2``), and the re-openable
recorded difference at the cutover (``balance:X-f3c``).

Sign convention, stated once: :attr:`BankStatementLine.amount` is SIGNED and
positive means money ENTERING the account, matching
``cash_ledger.settled_cash_leg`` exactly so a later match compares two figures
that already agree about direction.  Both of the developer's sources use that
convention natively (OFX ``TRNAMT``, and the CSV's Credit / Debit pair), so no
adapter has to invert anything.
"""

from app.extensions import db
from app.models.mixins import (
    AccountScopedMixin,
    CreatedAtMixin,
    UserScopedMixin,
)


class AccountExternalIdentity(AccountScopedMixin, UserScopedMixin,
                              CreatedAtMixin, db.Model):
    """Which account at a SOURCE is this Shekel account.

    Ruling **R-FP**: the importer "needs a one-time account mapping -- the
    export's ``ACCTID`` to the Shekel account -- and that mapping is a fact, not
    a guess."  This is where the fact lives.

    **It is RECORDED by the user's own choice and then CHECKED, never
    inferred.**  On the first import the user says which account a file is for
    and the file's own identity is written here; on every import after it, the
    file's identity is compared against the recorded one and a disagreement
    REFUSES the import.  Inferring the account from the file instead would make
    a mis-typed export silently post one account's statement onto another --
    and the two sources cannot even be compared for equality, because SECU's
    CSV masks the account number (``******3820``) where its OFX spells it out
    (``40943820``).

    Columns:
        account_id  -- the Shekel account (from :class:`AccountScopedMixin`).
        user_id     -- its owner (from :class:`UserScopedMixin`), held equal to
                       the account's by ``fk_account_external_identities_owner``
                       so it is a co-located key rather than a copy.  It exists
                       because UNIQUENESS IS PER OWNER: see below.
        source_id   -- the adapter the identity was read by
                       (``ref.statement_sources``).
        external_account_id -- what that source calls the account.

    **The key is per SOURCE, not per institution, and that is deliberate
    honesty rather than a limitation accepted.**  SECU's CSV and its OFX are
    two adapters over one real account, and they would produce two rows here.
    Keying on the institution instead would require deciding that
    ``******3820`` and ``40943820`` name the same account -- which is a guess,
    which is exactly what this table exists not to make.  Two rows pointing at
    one ``account_id`` is the truthful record: each says what its own source
    calls this account.
    """

    __tablename__ = "account_external_identities"
    __table_args__ = (
        # One external account maps to at most ONE of THIS OWNER'S accounts.
        # The arm that makes importing the card's export into Checking
        # refusable by the DATABASE rather than by a reviewer noticing.
        #
        # **Scoped by owner, and that is not decoration.**  A GLOBAL key over
        # ``(source_id, external_account_id)`` is wrong on a low-entropy value:
        # this adapter's identifier is SECU's MASK (``******3820``), so two
        # owners at one credit union collide on the last four digits with
        # probability 1/10,000 per pair -- and the loser could never import
        # their own statements, while the refusal would disclose that some
        # other account in the system had claimed their number.  Per owner, the
        # only row you can collide with is your own, which is a fact you are
        # entitled to be told about.
        db.UniqueConstraint(
            "user_id", "source_id", "external_account_id",
            name="uq_account_external_identities_owner_source_account",
        ),
        # ...and one Shekel account has at most one identity per source, so
        # "what does this source call this account" has exactly one answer.
        db.UniqueConstraint(
            "account_id", "source_id",
            name="uq_account_external_identities_account_source",
        ),
        # This row's owner IS its account's, guaranteed rather than maintained
        # -- the construction ``fk_transaction_entries_parent_account`` uses,
        # keyed onto ``uq_accounts_id_user``.  Without it ``user_id`` would be
        # a copy some writer has to keep in step, and the uniqueness above
        # would be scoped by a column that could be set wrong.
        db.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_account_external_identities_owner",
            ondelete="CASCADE",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(
        db.Integer,
        db.ForeignKey("ref.statement_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_account_id = db.Column(db.String(64), nullable=False)

    source = db.relationship("StatementSource", lazy="joined")

    def __repr__(self):
        return (
            f"<AccountExternalIdentity account={self.account_id} "
            f"external={self.external_account_id}>"
        )


class StatementImport(AccountScopedMixin, UserScopedMixin, CreatedAtMixin,
                      db.Model):
    """One act of importing a statement, and what that act did.

    The provenance every recorded line points back to: who imported what, when,
    from which file, and how much of it was new.  A line names the import that
    FIRST recorded it, so re-importing an overlapping span leaves the original
    provenance intact and records a second import that added nothing -- which
    is what "re-importing a file cannot duplicate" looks like from this side.

    Columns:
        account_id   -- the account the statement is for
                        (:class:`AccountScopedMixin`).
        user_id      -- who performed the import (:class:`UserScopedMixin`).
        source_id    -- the adapter that parsed the file.
        file_name    -- the uploaded file's own name, for the user to recognise
                        the import by.  Provenance only; nothing keys on it.
        file_digest  -- SHA-256 of the uploaded BYTES.  Also provenance: it
                        answers "is this the same file I imported before"
                        exactly, where the name cannot.  Deliberately NOT
                        unique -- re-uploading an identical file is a legal and
                        harmless act that records 0 new lines, and refusing it
                        would trade a truthful no-op for an error message.
        period_start / period_end -- the span the file covers, taken from the
                        lines themselves rather than from any header, because a
                        header that disagrees with its own contents is a thing
                        that happens and the lines are what was recorded.
        line_count   -- lines the file contained.
        recorded_count -- lines this import actually wrote.  The difference
                        between the two is the overlap with what was already
                        known, and showing it is what makes idempotency
                        VISIBLE rather than merely true.
        stated_balance / stated_balance_on -- what the file's OWN header claims
                        the account held, and the day it names.  A CLAIM, kept
                        verbatim and never rewritten.  Both-or-neither,
                        enforced by
                        ``ck_statement_imports_stated_balance_paired``.
        balance_effective_on -- the day that claimed figure is actually the
                        balance FOR, solved from the file's own lines (plan
                        step ``bank_import:X-f6e-1``, ruling **R-GF**).  NULL
                        where the file's own lines cannot reach the day it
                        claims, which a DATE-RANGE export always is.
        balance_evidence_id -- how strongly that figure is HELD
                        (``ref.statement_balance_evidence``): proved by the
                        file's own chain, corroborated by other recorded
                        statements, or confirmed by nothing.  It is the
                        WEAKEST link in the chain behind the figure, so an
                        anchor solved against an unconfirmed opening is itself
                        unconfirmed.  See
                        :class:`app.enums.StatementBalanceEvidenceEnum`.

    **The stated day is NOT the day the figure is for, and that is measured
    rather than defensive.**  SECU writes the balance as of the EXPORT INSTANT
    and labels it with the export's own day.  On the developer's 2026-08-21
    export the header reads ``Balance as of 08/21/2026,2501.310000`` while the
    file's last line is 08-18 and ``2501.31`` is 08-18's closing; on the
    2026-08-16 export it reads ``$4,747.63``, which is 2026-08-13's closing,
    over a file listing two 2026-08-14 lines worth ``-$1,006.72``.  The claim
    and the day it is FOR are therefore two facts, so the file's own words stay
    in ``stated_balance_on`` and the solved day stands in
    ``balance_effective_on`` beside it.

    **``opening_balance`` and ``closing_balance`` were DROPPED at that step**,
    and dropping them is the point rather than a tidy-up: ``closing`` is
    ``opening + Sigma(lines)`` and ``opening`` is
    ``stated - Sigma(lines up to the effective day)``, so both were derived
    values stored beside their own source with nothing reconciling the three --
    the root cause several of this project's arcs exist to remove.  What is
    stored is the observation and how firmly it is held; every balance derives.
    """

    __tablename__ = "statement_imports"
    __table_args__ = (
        # The superkey a composite foreign key needs as its target, so
        # ``fk_bank_statement_lines_import_account`` below can hold a line's
        # account equal to its import's.  It constrains nothing on its own
        # (``id`` is already the primary key).
        db.UniqueConstraint(
            "id", "account_id", name="uq_statement_imports_id_account",
        ),
        db.CheckConstraint(
            "period_end >= period_start",
            name="ck_statement_imports_period_ordered",
        ),
        # A file with no lines is not an import, it is a parse that found
        # nothing, and the door refuses it before a row is written.  Stated
        # here too so no future writer can record one.
        db.CheckConstraint(
            "line_count > 0",
            name="ck_statement_imports_line_count_positive",
        ),
        # What was recorded is a SUBSET of what the file held.  Both arms
        # matter: a negative count is nonsense, and a count above
        # ``line_count`` would mean the import wrote lines the file did not
        # contain.
        db.CheckConstraint(
            "recorded_count >= 0 AND recorded_count <= line_count",
            name="ck_statement_imports_recorded_within_file",
        ),
        # The file's CLAIM is one fact in two columns, and what the import
        # made of it is a SECOND fact in two more.  A figure without its day
        # asserts nothing about an account, a day without a figure asserts
        # nothing at all, and a solved effective day without a basis is the
        # inference finding **N-241** deleted one table over: a fact whose
        # provenance a reader would have to guess from which other column
        # happens to be populated.
        db.CheckConstraint(
            "(stated_balance IS NULL) = (stated_balance_on IS NULL)",
            name="ck_statement_imports_stated_balance_paired",
        ),
        db.CheckConstraint(
            "(balance_effective_on IS NULL) = (balance_evidence_id IS NULL)",
            name="ck_statement_imports_balance_evidence_paired",
        ),
        # An anchor comes FROM a claim, so it cannot outlive one -- an
        # implication rather than a biconditional, and the asymmetry is
        # MEASURED.  A date-range export states the CURRENT balance rather
        # than the range's closing: the developer's 2026-01-02..2026-03-31
        # file, pulled 2026-08-23, states `$2,459.60` as of 08-23, which is
        # 145 days past its last line and `$255.41` from the `$2,715.01` its
        # own 139 lines imply.  Its claim is real and its anchor is
        # undeterminable, so a claim with no anchor is the honest state.
        db.CheckConstraint(
            "balance_effective_on IS NULL OR stated_balance IS NOT NULL",
            name="ck_statement_imports_anchor_needs_a_claim",
        ),
        # The solved day is one the FILE could have pinned, and both bounds are
        # structural truths about the solve rather than tolerances.  It ranges
        # over {the day before the first line} + {every day the file covers},
        # so ``period_start - 1`` is its floor and ``period_end`` its ceiling;
        # and a bank cannot state a balance for a day after the one it wrote on
        # the header, so the claimed day is its other ceiling.  Measured on the
        # developer's exports: 08-22 solves at 08-21 under a header dated
        # 08-22, and 08-16 at 08-13 under one dated 08-16.
        db.CheckConstraint(
            "balance_effective_on IS NULL OR ("
            "balance_effective_on >= period_start - 1 "
            "AND balance_effective_on <= period_end "
            "AND balance_effective_on <= stated_balance_on)",
            name="ck_statement_imports_effective_day_within_file",
        ),
        db.Index("idx_statement_imports_account", "account_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(
        db.Integer,
        db.ForeignKey("ref.statement_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    file_name = db.Column(db.String(255), nullable=False)
    file_digest = db.Column(db.String(64), nullable=False)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    line_count = db.Column(db.Integer, nullable=False)
    recorded_count = db.Column(db.Integer, nullable=False)
    # NULLABLE, because a source may state no balance at all -- and then this
    # import determines no opening and the three columns below are NULL with
    # it.  Every SECU export the developer holds states one.
    stated_balance = db.Column(db.Numeric(12, 2))
    stated_balance_on = db.Column(db.Date)
    # The day :attr:`stated_balance` is the balance FOR, solved from the lines
    # (plan step ``bank_import:X-f6e-1``).  NOT a copy of
    # :attr:`stated_balance_on`: on the developer's own 2026-08-16 export the
    # two are three days apart.
    balance_effective_on = db.Column(db.Date)
    balance_evidence_id = db.Column(
        db.Integer,
        db.ForeignKey("ref.statement_balance_evidence.id", ondelete="RESTRICT"),
    )

    source = db.relationship("StatementSource", lazy="joined")
    balance_evidence = db.relationship("StatementBalanceEvidence", lazy="joined")
    lines = db.relationship(
        "BankStatementLine", back_populates="statement_import",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    def __repr__(self):
        return (
            f"<StatementImport account={self.account_id} "
            f"{self.period_start}..{self.period_end} "
            f"{self.recorded_count}/{self.line_count} new>"
        )


class BankStatementLine(db.Model):
    """One line a bank showed, recorded as the bank stated it.

    The app's own record of a line it did not author.  Nothing here is derived
    from a Shekel row and nothing here is edited afterwards: a statement line is
    an OBSERVATION, and the whole point of ruling **R-FL** was that an
    observation is what the app was missing.

    Columns:
        account_id   -- the account the line belongs to.  Held equal to its
                        import's account by
                        ``fk_bank_statement_lines_import_account``, so it is a
                        co-located key rather than a copy a writer maintains --
                        the same shape ``transaction_entries.account_id`` takes
                        against its parent transaction.
        import_id    -- the import that FIRST recorded this line.
        posted_on    -- the day the bank posted it.  **This is the fact the
                        whole arc exists to obtain**: measured against the
                        developer's export, only 33 of 110 matched movements
                        carried the day the bank posted them (finding
                        **N-173**).
        transaction_on -- the day the bank STATED the transaction itself
                        happened, or ``None`` where the source states none.
                        **The NULL is a fact and not a gap** (plan step
                        ``bank_import:X-f6a-3a``): it carried a COPY of
                        :attr:`posted_on` until then, so nothing downstream
                        could tell an OBSERVED swipe day from a restatement of
                        the clearing day -- a derived value stored beside its
                        own source with nothing reconciling the two, which is
                        the root cause three of this project's arcs exist to
                        remove.  A match writes this day onto a purchase's
                        ``purchased_on`` (ruling **R-FW**), and writing a
                        clearing day there would claim every card purchase was
                        made on the day it cleared.
        amount       -- signed, positive INTO the account (see the module
                        docstring).
        description  -- what the bank called it, verbatim.
        merchant_id  -- the :class:`~app.models.merchant.Merchant` this line
                        was with, or ``None`` where the source names none.
                        **The one column here that a rule MATCHES on** (plan
                        step ``bank_import:X-f6a-3d``): a
                        :class:`~app.models.merchant_rule
                        .MerchantRule` is keyed by the same row, so
                        *lines from this merchant go in this budget line* is a
                        fact the owner states once.  It held the bank's string
                        itself until plan step ``bank_import:X-gd-1``, when the
                        merchant became a row -- so the string lives once and
                        the two tables agree by id rather than by comparing two
                        independently-widened copies of it.  What the ADAPTER
                        reads is still the source's own merchant FIELD rather
                        than a token parsed out of :attr:`description` -- see
                        below -- and ``statement_import._record`` is what turns
                        that string into this row.
        source_category -- the bank's OWN category string, kept as provenance.
                        It is the bank's opinion about a merchant, not a Shekel
                        category, and treating it as one would be a reference
                        value that no ``ref`` table governs.  **It may never
                        SUPPLY an answer, and since ruling R-GJ it may REQUIRE
                        one** (plan step ``bank_import:X-ga``): a merchant a
                        source files under a card-payment category has no
                        create-a-purchase arm until the owner says where it
                        goes -- whichever answer they give.  That is the whole
                        of the exception, and it is narrow because the opinion
                        is measurably wrong: SECU files the developer's Van
                        Loan car payment under the same words as the Capital
                        One card payments, 7 of the 22 lines carrying it.  The
                        vocabulary is keyed by ADAPTER in
                        ``statement_match._vocabulary``; nothing reads this
                        column to decide a destination, a figure or a day.
        external_id  -- the source's own id for the line (OFX ``FITID``) where
                        it has one.  CORROBORATION, not identity -- see below.
        sequence_in_group -- the ordinal that completes the identity key.
        running_balance -- the balance after this line, where the source
                        carries one.  Recorded because it is what lets an
                        import VERIFY itself (see
                        ``statement_import.verify_running_balance``).

    **A line's stored IDENTITY is ``(account_id, posted_on, amount,
    sequence_in_group)``**, and the ordinal is what makes that key total.  Two
    genuinely distinct charges can share a day and an amount -- the same coffee
    twice -- and a key without the ordinal would reject the second as a
    duplicate, which is silent money loss on exactly the shape a duplicate
    detector is supposed to protect.

    **The ordinal is a SURROGATE this app mints, and no re-import compares
    against it** (plan step ``bank_import:X-f6a-4``).  Three of the key's four
    terms are facts the bank stated; this one is not, and the write door used
    to compare an incoming line against whatever sat at its ordinal -- treating
    an app-assigned number as though the bank had supplied it, which is a
    derived value stored beside its source with nothing reconciling the two.
    Measured against the shipped code 2026-08-20, that refused a whole file on
    two events that were not restatements at all: two same-day same-amount
    lines re-ordered between exports, and a genuinely NEW line the bank
    INSERTED ahead of a recorded one.  A re-import now reconciles a
    ``(posted_on, amount)`` GROUP as a set, pairing on the wording the bank
    wrote (:func:`app.services.statement_import.pair_by_statement`), and mints
    an ordinal only for a line it has decided is new
    (:func:`app.services.statement_import.fresh_ordinals`).  What this key
    still guarantees is that every recorded line has a distinct, stable
    address, which is the whole of what a surrogate owes.

    **``external_id`` is corroboration rather than identity, and that is
    measured.**  Across two SECU exports twelve days apart the positional key
    above reproduced the ``FITID`` key exactly -- 0 keys in only one export, 0
    disagreeing ids, over 342 shared lines -- so identity costs nothing by not
    depending on it, while a source that HAS one still cannot write two lines
    claiming it (``uq_bank_statement_lines_external_id``).  One identity rule
    serves every adapter, including ``X-f6b``'s, instead of one rule per format.

    **The merchant is a FACT the adapter states, not a token a reader parses,
    and the NULL is the source saying it names none** (plan step
    ``bank_import:X-f6a-3d``).  It was
    ``statement_match._offers.merchant_of(description)``, read at render time,
    and that was right for what it fed: a form's name box, where a wrong parse
    costs a badly-named row.  Keying a RULE on it is a stronger claim than a
    display default can carry, in one specific way -- that reader is TOTAL, so
    a source with no merchant token falls back to the whole description, and
    SECU's own OFX truncates 326 of its 361 descriptions to exactly 32
    characters.  Every one of those would key one rule, which would then fire
    on every merchant behind them.  A NULL keys nothing, so a source that
    cannot name a merchant offers no rule rather than a wrong one -- the same
    direction a missing fact has to fail in that :attr:`transaction_on` already
    fails in.
    **It is read from the source's own merchant FIELD** (for SECU's CSV, the
    parenthesised trailing token of the Description CELL) rather than from
    :attr:`description`, which is the ``Description | Memo`` join -- so a
    user's own memo ending in parentheses cannot become the key a rule matches
    on.  That bound is structural rather than guarded, exactly as
    ``_secu_csv._stated_transaction_day``'s is.

    **There is deliberately no ``transaction_on <= posted_on`` CHECK.**  The
    obvious constraint is false on real data: 2 of 361 lines in the developer's
    own SECU export carry an OFX ``DTUSER`` one day AFTER their ``DTPOSTED``
    (both ACH deposits, 2026-02-24 and 2026-03-18).  A constraint that a real
    statement violates would make the truth unimportable.
    **What depends on that day being the earlier one is therefore a READER's
    guard, not the schema's** (plan step ``bank_import:X-f6a-3a``): a match
    corrects a purchase's ``purchased_on`` to this day, and
    ``entry_service.update_entry`` refuses the pair a later one would make --
    so the proposer declines the pairing rather than the table refusing the
    line.
    """

    __tablename__ = "bank_statement_lines"
    __table_args__ = (
        # The SUPERKEY ``statement_match_members`` names to prove its own
        # ``account_id`` is this line's (plan step ``bank_import:X-f6a-2``).  It
        # constrains nothing -- ``id`` is already the primary key, so this key
        # can reject no row -- and exists only because PostgreSQL requires a
        # UNIQUE over exactly the referenced columns before a composite foreign
        # key may target them.
        db.UniqueConstraint(
            "id", "account_id", name="uq_bank_statement_lines_id_account",
        ),
        # THE IDENTITY.  Re-importing an overlapping span cannot duplicate a
        # line, structurally rather than by the importer remembering to check.
        db.UniqueConstraint(
            "account_id", "posted_on", "amount", "sequence_in_group",
            name="uq_bank_statement_lines_identity",
        ),
        # A source that HAS its own id may not claim one twice.  Partial,
        # because most adapters carry no external id and a NULL is not a claim.
        db.Index(
            "uq_bank_statement_lines_external_id",
            "account_id", "external_id",
            unique=True,
            postgresql_where=db.text("external_id IS NOT NULL"),
        ),
        # This line's account IS its import's, guaranteed rather than
        # maintained -- the same construction
        # ``fk_transaction_entries_parent_account`` uses.  CASCADE so that
        # deleting an account takes its imports and their lines with it -- and
        # since plan step ``bank_import:X-f6a-4`` so that DELETING AN IMPORT
        # takes the lines it first recorded, which is the mechanism its repair
        # door rests on (``statement_import.delete_import``).  The comment here
        # used to justify the cascade by "there is no door in ``app/`` that
        # deletes an import on its own", which that step made false while
        # leaving the cascade exactly as right.
        db.ForeignKeyConstraint(
            ["import_id", "account_id"],
            ["budget.statement_imports.id",
             "budget.statement_imports.account_id"],
            name="fk_bank_statement_lines_import_account",
            ondelete="CASCADE",
        ),
        db.CheckConstraint(
            "sequence_in_group >= 0",
            name="ck_bank_statement_lines_sequence_non_negative",
        ),
        # A statement line MOVES money, and its figures are REAL numbers.
        # ``docs/coding-standards.md`` requires a CHECK on every financial
        # column; the adapter's refusal of a line stating no amount is the
        # Python half of the same rule.
        #
        # **The ``< 'NaN'`` term is the part that is not obvious, and a first
        # draft of this constraint got it wrong.**  PostgreSQL's ``numeric``
        # accepts ``NaN`` and orders it ABOVE every real number, so
        # ``NaN <> 0`` is TRUE and ``NaN = NaN`` is TRUE -- a plain non-zero
        # test admits it.  Since NaN sorts greatest, ``x < 'NaN'`` is true for
        # every real value and false for NaN itself, which is what makes a NaN
        # amount unrepresentable rather than merely unreached.  It matters
        # because a NaN amount compares equal to nothing (invisible to every
        # matcher), poisons ``SUM()`` over the account, and raises inside the
        # money display macro -- so the page 500s on every later load.
        db.CheckConstraint(
            "amount <> 0 AND amount < 'NaN'::numeric "
            "AND (running_balance IS NULL "
            "OR running_balance < 'NaN'::numeric)",
            name="ck_bank_statement_lines_amount_real_nonzero",
        ),
        # This line's merchant is one of THIS ACCOUNT's, structurally (plan
        # step ``bank_import:X-gd-1``).  Composite rather than a bare
        # ``merchant_id`` FK for the reason
        # ``fk_bank_statement_lines_import_account`` is composite: otherwise
        # "is this merchant on this account" is a reader's check that can be
        # forgotten.  ``MATCH SIMPLE`` (PostgreSQL's default) is what lets it
        # sit on a nullable column -- a line whose ``merchant_id`` is NULL
        # satisfies it whatever ``account_id`` says, which is the source
        # naming none.  The blank-name rule it replaces now lives once, on
        # ``ck_merchants_name_not_blank``.
        db.ForeignKeyConstraint(
            ["merchant_id", "account_id"],
            ["budget.merchants.id", "budget.merchants.account_id"],
            name="fk_bank_statement_lines_merchant_account",
        ),
        # The walk reads a whole account in posted-day order.
        db.Index(
            "idx_bank_statement_lines_account_day",
            "account_id", "posted_on",
        ),
        # The review screen groups an account's unexplained lines BY MERCHANT
        # and resolves one rule per group (plan step ``bank_import:X-f6a-3d``,
        # ``statement_match._rules``).  Partial, because a NULL merchant joins
        # no rule and so is never looked up by this column.
        db.Index(
            "idx_bank_statement_lines_account_merchant",
            "account_id", "merchant_id",
            postgresql_where=db.text("merchant_id IS NOT NULL"),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # No direct FK: the composite key above reaches ``budget.accounts`` through
    # the import, which is what holds the two accounts equal.  Same shape, same
    # reason, as ``transaction_entries.account_id``.
    account_id = db.Column(db.Integer, nullable=False)
    import_id = db.Column(db.Integer, nullable=False)
    posted_on = db.Column(db.Date, nullable=False)
    # NULLABLE, and the NULL means "this source states no separate transaction
    # day" rather than "unknown".  Measured on the developer's own 2026-08-16
    # SECU export: the CSV states one on 182 of 361 lines and the OFX states
    # none at all -- its ``DTUSER`` equals ``DTPOSTED`` on 359 of 361 and is one
    # day LATER on the other two -- so a column that is never NULL would be a
    # copy on at least half of every statement.
    transaction_on = db.Column(db.Date)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    # NULLABLE, and the NULL means "this source names no merchant" rather than
    # "unknown" -- see the class docstring for why that direction is the safe
    # one on the fact a rule matches against.  No direct single-column key:
    # the merchant is reached through a composite that also holds the ACCOUNT
    # equal, the same shape ``import_id`` above takes.
    merchant_id = db.Column(db.Integer)
    source_category = db.Column(db.String(100))
    external_id = db.Column(db.String(64))
    # NO server default, deliberately.  The table is new and empty, so there
    # is no backfill to serve -- and a default on a component of the IDENTITY
    # key would let a future writer that forgets to compute the ordinal write a
    # plausible row instead of failing.
    sequence_in_group = db.Column(db.SmallInteger, nullable=False)
    running_balance = db.Column(db.Numeric(12, 2))

    statement_import = db.relationship(
        "StatementImport", back_populates="lines",
        foreign_keys=[import_id, account_id],
    )
    # **Eager and VIEWONLY** (plan step ``bank_import:X-gd-1``).  Eager because
    # every reader that has a line wants what its merchant is CALLED -- the
    # review screen renders 91 of them at once, and a lazy load there is the
    # N+1 finding **N-309** already paid for.  Viewonly because the writer sets
    # ``merchant_id`` from a resolved map (``statement_import._record``), so
    # nothing assigns through this and the two relationships sharing
    # ``account_id`` cannot contend over persisting it.
    #
    # **A writer that sets ``merchant_id`` may not then read
    # :attr:`merchant_name` on the same instance**, and that is not a rule
    # about ``viewonly`` -- it is what a loaded many-to-one does in any
    # session: assigning the FK column does not move it, so the stale name
    # survives until the instance is expired.  It cost a real test failure on
    # 2026-08-25, where the arm under test was correct and the assertion read
    # the object rather than the row.  No writer in ``app/`` reads it: both
    # writers are in ``statement_import._record``, which sets the column and
    # returns counts.  The direction the seam runs in is the whole reason this
    # is viewonly.
    merchant = db.relationship(
        "Merchant", foreign_keys=[merchant_id, account_id],
        lazy="joined", viewonly=True,
    )

    @property
    def merchant_name(self) -> "str | None":
        """Return what the source CALLS this line's merchant, or ``None``.

        The label half of the fact :attr:`merchant_id` is the key half of, so
        a caller holding this row does not have to know that a merchant is a
        row to print its name.  ``None`` exactly when :attr:`merchant_id` is,
        which is the source naming none.
        """
        return self.merchant.name if self.merchant is not None else None

    def __repr__(self):
        return (
            f"<BankStatementLine {self.posted_on} {self.amount} "
            f"'{self.description[:24]}' ({self.id})>"
        )
