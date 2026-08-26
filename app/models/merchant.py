"""
Shekel Budget App -- Merchant Model (budget schema)

WHO one of this account's statement lines was with, as a ROW rather than as a
string repeated on every line that names it (plan step ``bank_import:X-gd-1``,
ruling **R-GR**).

**It exists because a RULE is keyed on it.**  A merchant string on a bank line
is provenance, like ``description`` and ``source_category`` beside it -- the
source's own words about that one line, and nothing joins to it.  The moment
the owner may say *lines from this merchant go in this budget line*, the
merchant stops being a word on a line and becomes the SUBJECT of a stored
decision, and a subject that two tables agree about is a row.

**What the promotion actually buys, stated so it can be checked.**

  * The scope check stops being load-bearing.  A rule could name any string a
    caller liked, and ``statement_match._rules._refuse_unknown_merchants``
    was the only thing between that and a stored row; it compared the
    submission against a DISTINCT over every recorded line.  A rule now names
    a ``merchant_id``, and ``fk_merchant_rules_merchant_account``
    refuses one that is not this account's -- so the refusal survives as a
    SENTENCE for a stale page, in the shape ``_rules._checked_template``
    already has, and no longer as the thing that makes the rule correct.
  * *Which merchants may be asked about* becomes a read of one table.  It was
    the UNION of two derivations -- every merchant this account's lines name,
    plus every merchant already answered for -- and the second half existed
    because deleting an import took a merchant's lines and would otherwise
    have made its rule unwithdrawable.  A merchant row OUTLIVES its lines, so
    the union is the table.
  * The join is by id.  *IDs for logic, strings for display* is this project's
    rule for ``ref`` tables, and a rule matching on a NAME is the same
    substitution one tier out: ``merchant_rules`` and
    ``bank_statement_lines`` each held their own 100-character copy of the
    string, matched by equality, with nothing holding the two widths or the
    two spellings together.

**A merchant SURVIVES the lines that named it, and that is the whole property
the union above rests on** -- a stated answer has to stay readable and
restatable after its lines are gone.  **It is not immortal, and the difference
is the answer.**  Deleting an IMPORT sweeps this account's merchants that no
surviving line names AND no stated answer is about
(``statement_import._undo._forget_orphan_merchants``), because such a row
preserves nothing: no rule is keyed on it, the section does not render it, and
nothing else can reach it.  Without that sweep the table had no ceiling at all
-- an owner could upload a file naming any number of unseen merchants, delete
the import, and keep the merchants permanently, once per upload.  That is the
hazard ``_rules._refuse_unknown_merchants`` was written for, which MOVED here
when the rule's key became a foreign key, and an adversarial security review
measured it on 2026-08-25.  An account's deletion takes every merchant through
:class:`~app.models.mixins.AccountScopedMixin`'s cascade.

**Both referrers name it with the DEFAULT ``NO ACTION``, and the pair of
consequences was measured rather than reasoned about.**  On a clone of the
developer's own database, 2026-08-25: deleting a merchant a recorded line names
is REFUSED (``fk_bank_statement_lines_merchant_account``), which is the truth --
a line's merchant is not a thing that can vanish out from under it -- and
deleting the whole ACCOUNT still SUCCEEDS, because every cascade of that one
statement completes before the referential check runs.  ``RESTRICT`` would have
given the first and might not have given the second, since it forbids the
deferral that makes the second work; ``CASCADE`` would have declared that
deleting a merchant deletes bank lines, which is false of what this app does
and dangerous if it ever became reachable.

**Per ACCOUNT, not per owner, and that is the same key
``merchant_rules`` already carries.**  A statement is one bank's record
of ONE account, so ``Food Lion`` as SECU spells it on Checking and ``Food
Lion`` as a card issuer spells it are two rows -- which is what lets a rule on
one account be a different answer from a rule on the other, the property
ruling **R-GA** names when it says the key carries the account.  Whether two
sources ever name ONE merchant is a real question with a real subject now, and
it is ``bank_import:X-f6b``'s: it opens when a SECOND source exists to
disagree with the first.
"""

from app.extensions import db
from app.models.mixins import AccountScopedMixin


class Merchant(AccountScopedMixin, db.Model):
    """One merchant this account's statements have named.

    Columns:
        account_id -- the account whose statements name it
            (:class:`~app.models.mixins.AccountScopedMixin`).
        name -- what the SOURCE calls it, verbatim.  **Not case-folded and not
            normalized**: two spellings from one bank are two merchants, which
            is honest -- deciding that ``Amazon`` and ``AMAZON`` are one is a
            guess, and nothing here makes guesses.  Measured on the
            developer's own 378 recorded lines: 62 distinct merchants, **0
            pairs differing only by case**.

    **It carries no ``user_id``**, for the reason
    :class:`~app.models.statement_import.BankStatementLine` carries none: the
    account IS the ownership statement, and a second copy of it would be a
    fact a writer maintains rather than one the schema holds.  Every reader
    reaches a merchant through an account the route has already proved.

    **It carries no timestamp.**  When a merchant was first seen is the
    earliest ``posted_on`` of the lines that name it, and a stored copy of a
    derived value beside no reconciler is the root cause several of this
    project's arcs exist to remove.
    """

    __tablename__ = "merchants"
    __table_args__ = (
        # THE IDENTITY.  One row per name per account, so resolving a line's
        # merchant is an upsert that cannot mint a second row for a name this
        # account already knows -- structurally rather than by the importer
        # remembering to look first.
        db.UniqueConstraint(
            "account_id", "name", name="uq_merchants_account_name",
        ),
        # The SUPERKEY both referrers name so their own ``account_id`` is this
        # merchant's.  It constrains nothing -- ``id`` is already the primary
        # key -- and exists only because PostgreSQL requires a UNIQUE over
        # exactly the referenced columns before a composite foreign key may
        # target them.  The same construction, for the same reason, as
        # ``uq_bank_statement_lines_id_account``.
        db.UniqueConstraint(
            "id", "account_id", name="uq_merchants_id_account",
        ),
        # A merchant is a NAME or this row keys nothing.  The rule
        # ``ck_bank_statement_lines_merchant_not_blank`` used to state on the
        # column this one replaces, kept where the string now lives once.
        # ``_secu_csv._stated_merchant`` answers ``None`` for the same input,
        # so the adapter and the table state one rule.
        db.CheckConstraint(
            "btrim(name) <> ''", name="ck_merchants_name_not_blank",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # 100 because that is what ``_secu_csv._MERCHANT`` will match and what the
    # column this replaces held.  It is now the ONE place the width is stated:
    # a key stored narrower than its source would silently fail to match the
    # longest merchants, which is what two independent 100s were one edit away
    # from becoming.
    name = db.Column(db.String(100), nullable=False)

    # **No relationship BACK to either referrer, deliberately.**  A merchant
    # has no interest in which lines named it -- every reader travels the other
    # way -- and a collection here would be a lazy load of a whole statement's
    # lines from any row that touched one.  The other direction is declared
    # where it is needed and used: ``BankStatementLine.merchant`` is eager and
    # viewonly, because a reader holding a line always wants the name; the
    # RULE table has none at all, and reads its merchants in one statement
    # (``statement_match._rules.rules_for``) because it renders every
    # merchant on the account at once.

    def __repr__(self):
        return f"<Merchant account={self.account_id} {self.name!r} ({self.id})>"
