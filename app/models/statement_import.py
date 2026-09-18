"""
Shekel Budget App -- Statement Import Models (budget schema)

What a BANK said, recorded as fact.  Four tables, one subject (plan step
``bank_import:X-f6a``, ruling **R-FP**; the fourth at ``bank_import:X-f6b-1``,
ruling **R-BI10**):

  * :class:`AccountExternalIdentity` -- which account at a SOURCE is which
    account here.  The one-time mapping R-FP calls "a fact, not a guess".
  * :class:`StatementImport` -- one act of importing, and the window it
    declares it answers for.
  * :class:`BankStatementLine` -- one line the bank showed: the fact every
    source agrees on.
  * :class:`StatementLineSighting` -- what ONE import said about ONE line.
    A line is held by the sightings of the imports that showed it, and lives
    while any sighting does.

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

from sqlalchemy import and_, select

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
    """One act of importing a statement, and the window it DECLARES.

    The provenance every sighting points back to: who imported what, when,
    from which file, and which days the source said it was answering for.
    **A line is not owned by an import** (plan step ``bank_import:X-f6b-1``,
    ruling **R-BI10**): it is held by the SIGHTINGS of every import that
    showed it, so re-importing an overlapping span records a second sighting
    of each line it already holds and a second import whose window now also
    vouches for those days.

    Columns:
        account_id   -- the account the statement is for
                        (:class:`AccountScopedMixin`).
        user_id      -- who performed the import (:class:`UserScopedMixin`).
        source_id    -- the adapter that parsed the file.
        file_name    -- the uploaded file's own name, for the user to recognise
                        the import by.  Provenance only; nothing keys on it.
                        A feed sync's name is a label naming its window.
        file_digest  -- SHA-256 of the uploaded BYTES.  Also provenance: it
                        answers "is this the same file I imported before"
                        exactly, where the name cannot.  Deliberately NOT
                        unique -- re-uploading an identical file is a legal and
                        harmless act that records nothing new, and refusing it
                        would trade a truthful no-op for an error message.
        declared_start / declared_end -- the window the SOURCE declares this
                        import answers for (ruling **R-BAL71**): a CSV export
                        declares its first..last line day, because the file
                        states no range; a feed sync declares the window it
                        requested, so a quiet day inside it is covered and a
                        zero-line sync still covers what it asked for.  This
                        is what coverage reads
                        (``statement_import._balance.covered_runs``) and what
                        bounds a placed level (``budget.level_lies_within_file``).
                        *They were ``period_start`` / ``period_end`` -- the
                        line extremes, stored -- until X-f6b-1; for every
                        import recorded before it the extremes ARE what a CSV
                        declares, so the rename moved no value.*
        stated_balance / stated_balance_on -- what the file's OWN header claims
                        the account held, and the day it names.  A CLAIM, kept
                        verbatim and never rewritten.  Both-or-neither,
                        enforced by
                        ``ck_statement_imports_stated_balance_paired``.

    **``line_count`` and ``recorded_count`` were DELETED at X-f6b-1** (ruling
    **R-IY**: a derivable column is deleted, not maintained).  How many lines
    a file held is how many sightings this import wrote; how many were new is
    how many of those lines it was the FIRST to sight
    (:meth:`StatementLineSighting.first_import_of_each_line`); and the
    receipt still says both, computed by the door that knows them.  A stored
    count beside the rows it counts was a cache with no reconciler, and
    ``ck_statement_imports_line_count_positive`` went with it because a
    zero-line sync that states a balance is an observation, not a parse that
    found nothing.

    **The stated day is NOT the day the figure is for, and that is measured
    rather than defensive.**  SECU writes the balance as of the EXPORT INSTANT
    and labels it with the export's own day.  On the developer's 2026-08-21
    export the header reads ``Balance as of 08/21/2026,2501.310000`` while the
    file's last line is 08-18 and ``2501.31`` is 08-18's closing; on the
    2026-08-16 export it reads ``$4,747.63``, which is 2026-08-13's closing,
    over a file listing two 2026-08-14 lines worth ``-$1,006.72``.  The claim
    and the day it is FOR are therefore two facts, so the file's own words stay
    here and the solved day is a LEVEL.

    **What the import made of the claim is a row in the LEVEL RELATION, not
    two columns here** (plan step ``balance:X-bj-1``, rulings **R-IS** and
    **R-JN**).  ``balance_effective_on`` and ``balance_evidence_id`` lived on
    this row from ``bank_import:X-f6e-1`` until that step, and a release
    nulled them by UPDATE.  The bank's placement is an observation of the same
    quantity the owner's true-ups observe -- the account's balance at the
    close of a day -- so it is a :class:`~app.models.account.AccountAnchorHistory`
    row naming this import, its amount locked to :attr:`stated_balance` by
    ``fk_anchor_history_statement_import_claim`` over
    ``uq_statement_imports_id_stated_balance``, and its withdrawal an
    appended :class:`~app.models.anchor_release.AnchorRelease`.  An import
    whose header cannot be placed owns no level; one whose placement was
    released owns a level and a release.  The three CHECKs that paired and
    bounded the two columns went with them: the pairing is the level row's
    NOT NULL shape and its keys, and the bound (the solved day lies inside the
    declared window) is ``budget.level_lies_within_file``
    (:mod:`app.level_infrastructure`), attached to BOTH tables so neither the
    level nor this row's window can move outside the other.

    **``opening_balance`` and ``closing_balance`` were DROPPED at X-f6e-1**,
    and dropping them is the point rather than a tidy-up: ``closing`` is
    ``opening + Sigma(lines)`` and ``opening`` is
    ``stated - Sigma(lines up to the effective day)``, so both were derived
    values stored beside their own source with nothing reconciling the three --
    the root cause several of this project's arcs exist to remove.  What is
    stored is the claim; the observation and how firmly it is held are the
    level row's; every balance derives.
    """

    __tablename__ = "statement_imports"
    __table_args__ = (
        # The superkey a composite foreign key needs as its target, so
        # ``fk_statement_line_sightings_import_account`` can hold a
        # sighting's account equal to its import's.  It constrains nothing on
        # its own (``id`` is already the primary key).
        db.UniqueConstraint(
            "id", "account_id", name="uq_statement_imports_id_account",
        ),
        # The superkey a bank LEVEL's amount keys onto
        # (``fk_anchor_history_statement_import_claim``, plan step
        # ``balance:X-bj-1``).  It constrains nothing on its own (``id`` is the
        # primary key); it exists because PostgreSQL requires a UNIQUE over
        # exactly the referenced columns.  ``stated_balance`` is nullable, and
        # that is load-bearing: a NULL in a referenced column matches no
        # referencing row, so an import that states no balance can own no
        # level -- the rule ``ck_statement_imports_anchor_needs_a_claim`` used
        # to state in words, now a property of the key.
        db.UniqueConstraint(
            "id", "stated_balance",
            name="uq_statement_imports_id_stated_balance",
        ),
        db.CheckConstraint(
            "declared_end >= declared_start",
            name="ck_statement_imports_declared_ordered",
        ),
        # The file's CLAIM is one fact in two columns.  A figure without its
        # day asserts nothing about an account, and a day without a figure
        # asserts nothing at all.  What the import MADE of the claim -- the
        # day it is the balance for, and how firmly -- is a level row's
        # (plan step ``balance:X-bj-1``); the two CHECKs that paired those
        # columns here are that row's NOT NULL shape now, and the one that
        # bounded the solved day within the file is
        # ``budget.level_lies_within_file`` on both tables.  A date-range
        # export still records its claim and owns no level: the developer's
        # 2026-01-02..2026-03-31 file, pulled 2026-08-23, states `$2,459.60`
        # as of 08-23, 145 days past its last line and `$255.41` from the
        # `$2,715.01` its own 139 lines imply.
        db.CheckConstraint(
            "(stated_balance IS NULL) = (stated_balance_on IS NULL)",
            name="ck_statement_imports_stated_balance_paired",
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
    declared_start = db.Column(db.Date, nullable=False)
    declared_end = db.Column(db.Date, nullable=False)
    # NULLABLE, because a source may state no balance at all -- and then this
    # import determines no opening and the three columns below are NULL with
    # it.  Every SECU export the developer holds states one.
    stated_balance = db.Column(db.Numeric(12, 2))
    stated_balance_on = db.Column(db.Date)

    source = db.relationship("StatementSource", lazy="joined")
    # NO relationship to the level this import placed, to the releases naming
    # it, or to the sightings it wrote, deliberately: the first two tables
    # are append-only, and a relationship would let the unit of work emit
    # the SET NULL / DELETE the triggers refuse on the way to deleting this
    # row.  The database's own CASCADE and ``SET NULL
    # (released_by_import_id)`` are the disposal path -- the sightings go by
    # ``fk_statement_line_sightings_import_account``, and a line they were the
    # last to hold goes by ``budget.remove_line_left_unsighted``
    # (:mod:`app.sighting_infrastructure`) -- and the readers that want a
    # level join for it (``statement_import._balance.bank_levels``).

    #: The columns that tell two acts on one account apart, oldest first: the
    #: instant the import ran, then its id.  The id is load-bearing rather
    #: than decorative: ``created_at`` defaults to ``now()``, which in
    #: PostgreSQL is the TRANSACTION's start time, so two imports written in
    #: one transaction carry the identical instant and the id is the only
    #: thing that orders them.
    #:
    #: **ONE spelling, because four readers sort by it** (plan step
    #: ``bank_import:X-f6b-1``): the imports table and the hero's *last
    #: import* line read it newest-first, and the sighting relation reads it
    #: to say which import FIRST sighted a line
    #: (:meth:`StatementLineSighting.first_import_of_each_line`) and which
    #: sighted it LATEST (:attr:`BankStatementLine.current`).  A fifth
    #: spelling of one order is how two surfaces come to call different
    #: imports the newest.  Stated as names so :meth:`act_order` (a query)
    #: and :attr:`act_key` (a loaded row) read the one list.
    _ACT_ORDER = ("created_at", "id")

    @classmethod
    def act_order(cls) -> tuple:
        """Return the ``ORDER BY`` columns for the act order, oldest first.

        Returns:
            ``(StatementImport.created_at, StatementImport.id)``; a reader
            wanting newest-first applies ``.desc()`` to each.
        """
        return tuple(getattr(cls, name) for name in cls._ACT_ORDER)

    @property
    def act_key(self) -> tuple:
        """Return this act's place in the act order, as a sort key.

        Returns:
            ``(created_at, id)`` off this row.
        """
        return tuple(getattr(self, name) for name in self._ACT_ORDER)

    def __repr__(self):
        return (
            f"<StatementImport account={self.account_id} "
            f"{self.declared_start}..{self.declared_end}>"
        )


class BankStatementLine(AccountScopedMixin, db.Model):
    """One line a bank showed, recorded as the bank stated it.

    The app's own record of a line it did not author.  Nothing here is derived
    from a Shekel row and nothing here is edited afterwards: a statement line is
    an OBSERVATION, and the whole point of ruling **R-FL** was that an
    observation is what the app was missing.

    **A line is the BANK's fact, and what each source CALLED it is that
    source's SIGHTING** (plan step ``bank_import:X-f6b-1``, ruling
    **R-BI10**).  This row holds what every source agrees on -- the account,
    the day, the amount, the ordinal that tells two same-day same-amount lines
    apart, and the merchant KEY a rule fires on -- and one
    :class:`StatementLineSighting` per import that showed it holds that
    source's wording, id, stated transaction day, running balance and
    category.  A line lives while any sighting does
    (``budget.remove_line_left_unsighted``, :mod:`app.sighting_infrastructure`),
    which is what lets a second source start recording over the first's
    span without the two disagreeing about a line neither authored.  Until
    that step the wording lived HERE, so a different wording on a known
    day+amount was read as the bank RESTATING a line and refused the whole
    file (finding **N-303**).

    Columns:
        account_id   -- the account the line belongs to
                        (:class:`AccountScopedMixin`, since X-f6b-1: it
                        reached the account through its import's composite
                        key before, and the import no longer owns it); every
                        sighting's two composite keys hold the sighting's
                        account equal to this one and to its import's.
        posted_on    -- the day the bank posted it.  **This is the fact the
                        whole arc exists to obtain**: measured against the
                        developer's export, only 33 of 110 matched movements
                        carried the day the bank posted them (finding
                        **N-173**).
        amount       -- signed, positive INTO the account (see the module
                        docstring).
        merchant_id  -- the :class:`~app.models.merchant.Merchant` this line
                        was with, or ``None`` where no source has named one.
                        **The one column here that a rule MATCHES on** (plan
                        step ``bank_import:X-f6a-3d``): a
                        :class:`~app.models.merchant_rule
                        .MerchantRule` is keyed by the same row, so
                        *lines from this merchant go in this budget line* is a
                        fact the owner states once.  It held the bank's string
                        itself until plan step ``bank_import:X-gd-1``, when the
                        merchant became a row -- so the string lives once and
                        the two tables agree by id rather than by comparing two
                        independently-widened copies of it.  **It stays on the
                        LINE under the sighting relation** because it is the
                        app's KEY rather than a source's word: minted from the
                        first sighting that names a merchant word and absorbed
                        from a later one only while NULL
                        (``statement_import._record._absorb_gained_facts``);
                        the WORD each source used is that sighting's own
                        :attr:`StatementLineSighting.merchant`.
        sequence_in_group -- the ordinal that completes the identity key.

    **The facts a SOURCE states about a line are its sighting's**, read
    through :attr:`sightings` and exposed here once each so a reader holding a
    line does not have to know which sighting to ask: :attr:`description`,
    :attr:`source_category` and :attr:`running_balance` are the sighting of
    the LATEST import (:attr:`current`) -- what the bank calls the line now --
    and :attr:`transaction_on` is the EARLIEST day any sighting states,
    because that is the day a match writes onto a purchase's ``purchased_on``
    (ruling **R-FW**) and money cannot be spent after the earliest day a
    source says it was.  A source's own id (``external_id``) is exposed on no
    line property: it is corroboration this source's next import pairs on
    (``_record``), never a fact about the line.

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
    ``(posted_on, amount)`` GROUP as a set -- by this source's own id, then by
    the wording this source wrote, then by count against the lines another
    source showed (``statement_import._record._reconcile``) -- and mints an
    ordinal only for a line it has decided is new
    (:func:`app.services.statement_import.fresh_ordinals`).  What this key
    still guarantees is that every recorded line has a distinct, stable
    address, which is the whole of what a surrogate owes.

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
    the description, which is the ``Description | Memo`` join -- so a user's
    own memo ending in parentheses cannot become the key a rule matches on.
    That bound is structural rather than guarded, exactly as
    ``_secu_csv._stated_transaction_day``'s is.

    **There is deliberately no ``transaction_on <= posted_on`` CHECK** on a
    sighting.  The obvious constraint is false on real data: 2 of 361 lines
    in the developer's own SECU export carry an OFX ``DTUSER`` one day AFTER
    their ``DTPOSTED`` (both ACH deposits, 2026-02-24 and 2026-03-18).  A
    constraint that a real statement violates would make the truth
    unimportable.
    **What depends on that day being the earlier one is therefore a READER's
    guard, not the schema's** (plan step ``bank_import:X-f6a-3a``): a match
    corrects a purchase's ``purchased_on`` to this day, and
    ``entry_service.update_entry`` refuses the pair a later one would make --
    so the proposer declines the pairing rather than the table refusing the
    line.
    """

    __tablename__ = "bank_statement_lines"
    __table_args__ = (
        # The SUPERKEY ``statement_match_members`` and
        # ``statement_line_sightings`` name to prove their own ``account_id``
        # is this line's (plan step ``bank_import:X-f6a-2``).  It constrains
        # nothing -- ``id`` is already the primary key, so this key can reject
        # no row -- and exists only because PostgreSQL requires a UNIQUE over
        # exactly the referenced columns before a composite foreign key may
        # target them.
        db.UniqueConstraint(
            "id", "account_id", name="uq_bank_statement_lines_id_account",
        ),
        # THE IDENTITY.  Re-importing an overlapping span cannot duplicate a
        # line, structurally rather than by the importer remembering to check.
        db.UniqueConstraint(
            "account_id", "posted_on", "amount", "sequence_in_group",
            name="uq_bank_statement_lines_identity",
        ),
        db.CheckConstraint(
            "sequence_in_group >= 0",
            name="ck_bank_statement_lines_sequence_non_negative",
        ),
        # A statement line MOVES money, and its figure is a REAL number.
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
        # money display macro -- so the page 500s on every later load.  The
        # running balance carries the same term on its own table.
        db.CheckConstraint(
            "amount <> 0 AND amount < 'NaN'::numeric",
            name="ck_bank_statement_lines_amount_real_nonzero",
        ),
        # This line's merchant is one of THIS ACCOUNT's, structurally (plan
        # step ``bank_import:X-gd-1``).  Composite rather than a bare
        # ``merchant_id`` FK so that "is this merchant on this account" is
        # never a reader's check that can be forgotten.  ``MATCH SIMPLE``
        # (PostgreSQL's default) is what lets it sit on a nullable column -- a
        # line whose ``merchant_id`` is NULL satisfies it whatever
        # ``account_id`` says, which is no source having named one.  The
        # blank-name rule it replaces now lives once, on
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
    # ``account_id`` is the mixin's: a direct CASCADE key since X-f6b-1.  The
    # line reached the account through ``fk_bank_statement_lines_import_
    # account`` while an import owned it; a line held by sightings has no
    # single import to reach through, and a row whose account is a bare
    # integer until its first sighting arrives is a row a writer can put on
    # the wrong account.
    posted_on = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    # NULLABLE, and the NULL means "no source has named a merchant" rather
    # than "unknown" -- see the class docstring for why that direction is the
    # safe one on the fact a rule matches against.  No direct single-column
    # key: the merchant is reached through a composite that also holds the
    # ACCOUNT equal.
    merchant_id = db.Column(db.Integer)
    # NO server default, deliberately.  A default on a component of the
    # IDENTITY key would let a future writer that forgets to compute the
    # ordinal write a plausible row instead of failing.
    sequence_in_group = db.Column(db.SmallInteger, nullable=False)

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
        "Merchant",
        # By NAME, because ``account_id`` is the mixin's column and is not a
        # name in this class body.
        foreign_keys="[BankStatementLine.merchant_id, "
                     "BankStatementLine.account_id]",
        lazy="joined", viewonly=True,
    )
    # **Eager, for the reason :attr:`merchant` is** (plan step
    # ``bank_import:X-f6b-1``): every reader that has a line wants what the
    # bank CALLED it, and every one of those facts is a sighting's now.
    # ``joined`` rather than ``selectinload`` so a reader that loads N lines still
    # issues ONE statement -- the property
    # ``test_reading_every_lines_merchant_is_ONE_statement`` grades -- and a
    # list bounded by ``LIMIT`` is wrapped in a subquery by the ORM so the
    # bound counts LINES, not the joined rows.  Writable (not viewonly) so a
    # fixture may append to it; the app's one writer stages sightings by
    # their ids (``statement_import._record``).  ``delete, delete-orphan``
    # with ``passive_deletes`` so the unit of work agrees with the database
    # about disposal -- a sighting goes with its line -- rather than trying
    # to null a loaded child's NOT NULL key on the way; no door in ``app/``
    # deletes a line through the ORM (the trigger and the cascades do), so
    # this is what a fixture or a psql-less repair gets.
    sightings = db.relationship(
        "StatementLineSighting", back_populates="line",
        foreign_keys="[StatementLineSighting.line_id, "
                     "StatementLineSighting.account_id]",
        lazy="joined", cascade="all, delete-orphan", passive_deletes=True,
    )

    @property
    def merchant_name(self) -> "str | None":
        """Return what this line's merchant is CALLED, or ``None``.

        The label half of the fact :attr:`merchant_id` is the key half of, so
        a caller holding this row does not have to know that a merchant is a
        row to print its name.  ``None`` exactly when :attr:`merchant_id` is,
        which is no source having named one.
        """
        return self.merchant.name if self.merchant is not None else None

    @property
    def current(self) -> "StatementLineSighting":
        """Return the sighting of the LATEST import that showed this line.

        What the bank calls the line NOW.  Latest by
        :attr:`StatementImport.act_key` -- the import's instant, then its
        id -- and never by the sighting's own id, which is a surrogate the
        sequence hands out in write order and which a backfill wrote in line
        order.  A line always has one: ``budget.remove_line_left_unsighted``
        deletes a line whose last sighting goes, so a loaded line with an
        empty collection is a row this session staged and has not flushed a
        sighting for yet, and reading a source's fact off it is a caller's
        defect rather than a state to answer for.
        """
        return max(
            self.sightings,
            key=lambda sighting: sighting.statement_import.act_key,
        )

    @property
    def description(self) -> str:
        """Return what the bank calls this line, per its latest sighting."""
        return self.current.description

    @property
    def source_category(self) -> "str | None":
        """Return what the latest source filed this line under, or ``None``."""
        return self.current.source_category

    @property
    def running_balance(self) -> "Decimal | None":
        """Return the balance the latest source stated after this line, or ``None``."""
        return self.current.running_balance

    @property
    def transaction_on(self) -> "date | None":
        """Return the EARLIEST day any source says this line was made, or ``None``.

        The day a match writes onto a matched purchase's ``purchased_on``
        (ruling **R-FW**), so it is the tightest bound the sightings support:
        money cannot be spent after the earliest day a source states it was.
        ``None`` when no sighting states one, which is every source saying it
        names no separate transaction day.
        """
        stated = [
            sighting.transaction_on for sighting in self.sightings
            if sighting.transaction_on is not None
        ]
        return min(stated) if stated else None

    def __repr__(self):
        return (
            f"<BankStatementLine {self.posted_on} {self.amount} "
            f"x{self.sequence_in_group} ({self.id})>"
        )


class StatementLineSighting(db.Model):
    """What ONE import said about ONE bank line.

    Plan step ``bank_import:X-f6b-1``, ruling **R-BI10**: *a bank line is held
    by the sightings of the imports that showed it.*  The line is the bank's
    fact -- account, day, amount, ordinal; this row is one source's account of
    it: the wording that source wrote, the id it assigned, the day it says the
    money was spent, the balance it stated after the line and the category it
    filed the line under.  Two sources showing one line write two rows here
    and one line there, and the app stops calling a second wording a
    restatement.

    Columns:
        account_id   -- the line's account and the import's, held equal to
                        BOTH by the two composite keys below, so a sighting
                        cannot pair a line with another account's import.
        line_id      -- the :class:`BankStatementLine`.
        import_id    -- the :class:`StatementImport` that showed it.
        description  -- what this source called the line, verbatim.
        merchant     -- the merchant WORD this source named, or ``None`` where
                        it names none: the CSV's parenthesised token, the
                        feed's ``payee``.  Provenance of this sighting; the
                        KEY a rule fires on is the line's ``merchant_id``,
                        minted from the first sighting that names a word.
        transaction_on -- the day this source STATED the transaction itself
                        happened, or ``None`` where it states none.  **The
                        NULL is a fact and not a gap** (plan step
                        ``bank_import:X-f6a-3a``): a source that does not
                        distinguish the two days says so here rather than
                        restating the clearing day.
        external_id  -- this source's own id for the line (an OFX ``FITID``,
                        the feed's ``id``), or ``None``.  CORROBORATION that
                        this source's next import pairs on first
                        (``statement_import._record._reconcile``), never the
                        line's identity: a source that has one still cannot
                        claim it on two lines of one account -- the door
                        refuses a file restating a held id on another day or
                        amount (``_record._refuse_moved_ids``) -- and one that
                        has none is not thereby unidentifiable.
        running_balance -- the balance this source stated after the line, or
                        ``None`` where the export carries none.  A prefix sum
                        over the source's LISTING order, which is why two
                        sightings of one line may legitimately disagree
                        (``_record._refuse_restatement``); what verifies an
                        import against itself is the chain inside one file
                        (``statement_import.verify_running_balance``).
        source_category -- the source's OWN category string, kept as
                        provenance.  It is the bank's opinion about a
                        merchant, not a Shekel category; the one thing that
                        reads it is ``statement_match._vocabulary``, in SQL,
                        against that adapter's own vocabulary
                        (**bank_import:R-GJ**).

    **One row per ``(line, import)``**, which is the whole of what a re-import
    means: an import that shows a line the app already holds records that it
    showed it, and a line is *already known* to an import exactly when the
    import's sighting of it is not the line's first.  The two derived reads
    every counter needs -- which import FIRST sighted a line, and which lines
    ONE import alone holds -- are :meth:`first_import_of_each_line` and
    :meth:`sole_import_of_each_line`, stated once each here because both
    service packages read them and neither may import the other.

    **Audit-logged like its siblings, and NOT append-only**: a sighting is
    deleted with its import, which rulings **R-GG(e)** and **R-BAL51**
    already permit for a level.  When the LAST sighting of a line goes, the
    line goes with it (``budget.remove_line_left_unsighted``,
    :mod:`app.sighting_infrastructure`) -- so an import's deletion removes
    exactly the lines no other import vouches for, and a line cannot be
    STRANDED by a delete.  (It can still be inserted with no sighting, as
    the record door does for the instant between staging a fresh line and
    staging its sighting; what the trigger makes impossible is a line
    outliving its last sighting.)
    """

    __tablename__ = "statement_line_sightings"
    __table_args__ = (
        # THE identity of a sighting: one per import per line.  A re-import
        # of a file the app has already recorded writes one row per line here
        # and nothing on the line, and this key is what makes writing it twice
        # in one act impossible rather than checked.
        db.UniqueConstraint(
            "line_id", "import_id",
            name="uq_statement_line_sightings_line_import",
        ),
        # This sighting's account IS its line's, guaranteed rather than
        # maintained -- keyed onto ``uq_bank_statement_lines_id_account``.
        # CASCADE: a line that goes takes what was said about it.
        db.ForeignKeyConstraint(
            ["line_id", "account_id"],
            ["budget.bank_statement_lines.id",
             "budget.bank_statement_lines.account_id"],
            name="fk_statement_line_sightings_line_account",
            ondelete="CASCADE",
        ),
        # ...and its import's, keyed onto ``uq_statement_imports_id_account``.
        # CASCADE: deleting an import withdraws everything it said, and the
        # line survives exactly when another import still says something.
        db.ForeignKeyConstraint(
            ["import_id", "account_id"],
            ["budget.statement_imports.id",
             "budget.statement_imports.account_id"],
            name="fk_statement_line_sightings_import_account",
            ondelete="CASCADE",
        ),
        # The lookup the record door makes before it pairs: where THIS SOURCE
        # already holds each id the file states (``_record._held_ids``).
        # Partial, because most adapters carry no external id.  **Not
        # unique, and the line-level index it replaces was**: a re-import
        # re-sights a line under the same id, which is two rows here and one
        # line there.  The rule that a source names ONE line per id is the
        # door's (``_record._refuse_moved_ids``), pinned by its tests.
        db.Index(
            "idx_statement_line_sightings_account_external_id",
            "account_id", "external_id",
            postgresql_where=db.text("external_id IS NOT NULL"),
        ),
        # Every per-import count reads this way: the sightings one import
        # wrote, the lines it alone holds, the merchants its lines name.
        db.Index("idx_statement_line_sightings_import", "import_id"),
        # Every per-ACCOUNT derivation reads this way (the first and sole
        # import of each line, the counts, the record door's id lookup), and
        # the two composite keys index nothing of their own.  Trivial at one
        # CSV's 306 rows; the daily feed writes a row per line per sync.
        db.Index(
            "idx_statement_line_sightings_account_line",
            "account_id", "line_id",
        ),
        # The stated figure is a REAL number or absent, the same term the
        # line's own amount carries and for the same reason (see
        # ``ck_bank_statement_lines_amount_real_nonzero``).
        db.CheckConstraint(
            "running_balance IS NULL OR running_balance < 'NaN'::numeric",
            name="ck_statement_line_sightings_running_balance_real",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # No direct FK: the two composite keys above reach ``budget.accounts``
    # through the line and through the import, which is what holds the three
    # accounts equal.
    account_id = db.Column(db.Integer, nullable=False)
    line_id = db.Column(db.Integer, nullable=False)
    import_id = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(200), nullable=False)
    merchant = db.Column(db.String(100))
    transaction_on = db.Column(db.Date)
    external_id = db.Column(db.String(64))
    running_balance = db.Column(db.Numeric(12, 2))
    source_category = db.Column(db.String(100))

    line = db.relationship(
        "BankStatementLine", back_populates="sightings",
        foreign_keys=[line_id, account_id],
    )
    # **Eager and VIEWONLY**, the shape :attr:`BankStatementLine.merchant`
    # takes and for its reason: every reader that holds a sighting asks which
    # import it belongs to -- to order the sightings of one line
    # (:attr:`BankStatementLine.current`) and to know which SOURCE showed it
    # (``_record._reconcile`` pairs within a source) -- so a lazy load here is
    # one statement per sighting on every list surface; and viewonly because
    # :attr:`line` already persists ``account_id`` and two relationships
    # writing one column would contend.
    statement_import = db.relationship(
        "StatementImport", foreign_keys=[import_id, account_id],
        lazy="joined", viewonly=True,
    )

    @classmethod
    def of_its_line(cls):
        """Return the join clause onto the sighting's line, both key columns.

        ONE spelling of the composite join every reader that walks line ->
        sighting makes, so the account equality travels IN the join rather
        than being remembered per reader.

        Returns:
            The ``AND`` of the two column equalities.
        """
        return and_(
            cls.line_id == BankStatementLine.id,
            cls.account_id == BankStatementLine.account_id,
        )

    @classmethod
    def of_its_import(cls):
        """Return the join clause onto the sighting's import, both key columns.

        The sighting -> import half of :meth:`of_its_line`'s rule.

        Returns:
            The ``AND`` of the two column equalities.
        """
        return and_(
            cls.import_id == StatementImport.id,
            cls.account_id == StatementImport.account_id,
        )

    @classmethod
    def first_import_of_each_line(cls, account_id: int):
        """Return which import FIRST sighted each of *account_id*'s lines.

        The fact ``bank_statement_lines.import_id`` stored until X-f6b-1 --
        *the import that first recorded this line* -- as a derivation: for
        each line, the sighting whose import is earliest by
        :meth:`StatementImport.act_order`.  What *recorded N new line(s)*
        counts, what the rule pass files
        (``statement_match._filing._fresh_line_ids``) and what the hero's
        *last import* line reports.

        Args:
            account_id: The account whose lines to read.

        Returns:
            A subquery with columns ``line_id`` and ``import_id``, one row per
            sighted line of the account.  A reader joins it on ``line_id`` and
            filters ``import_id``.
        """
        return (
            select(cls.line_id, cls.import_id)
            .join(StatementImport, cls.of_its_import())
            .where(cls.account_id == account_id)
            .distinct(cls.line_id)
            .order_by(cls.line_id, *StatementImport.act_order())
            .subquery()
        )

    @classmethod
    def sole_import_of_each_line(cls, account_id: int):
        """Return which lines exactly ONE import holds, and which import.

        The lines that GO when that import is deleted -- and therefore the
        matches, skips and merchants its deletion takes with it
        (``statement_import._reads``).  A line another import also sighted
        stays, so it is absent here.

        Args:
            account_id: The account whose lines to read.

        Returns:
            A subquery with columns ``line_id`` and ``import_id``, one row per
            line with a single sighting.
        """
        return (
            select(cls.line_id, db.func.min(cls.import_id).label("import_id"))
            .where(cls.account_id == account_id)
            .group_by(cls.line_id)
            .having(db.func.count(cls.id) == 1)
            .subquery()
        )

    @classmethod
    def counts_by_import(cls, account_id: int):
        """Return, per import, how many lines it sighted and how many it sighted FIRST.

        The two figures ``statement_imports.line_count`` and
        ``recorded_count`` stored until X-f6b-1, as ONE aggregate over the
        sightings: what the imports table prints per row, what the hero's
        *last import* line prints for the newest, and what a receipt
        computes for the act it just performed.  The FIRST half reads
        :meth:`first_import_of_each_line`, so the three surfaces cannot
        count "new" three ways.

        Args:
            account_id: The account whose imports to count over.

        Returns:
            A ``SELECT`` with columns ``import_id``, ``sighted`` and
            ``first``, one row per import that wrote a sighting; a caller
            executes it and reads an absent import as ``(0, 0)``.
        """
        first = cls.first_import_of_each_line(account_id)
        return (
            select(
                cls.import_id,
                db.func.count(cls.id).label("sighted"),
                db.func.count(cls.id)
                .filter(first.c.import_id == cls.import_id)
                .label("first"),
            )
            .join(first, first.c.line_id == cls.line_id)
            .where(cls.account_id == account_id)
            .group_by(cls.import_id)
        )

    def __repr__(self):
        return (
            f"<StatementLineSighting line={self.line_id} "
            f"import={self.import_id} '{self.description[:24]}'>"
        )
