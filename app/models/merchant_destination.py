"""
Shekel Budget App -- Merchant Destination Model (budget schema)

WHERE the owner has said a merchant's spending goes.  One table, one subject
(plan step ``bank_import:X-f6a-3d``).

**It is a DECISION, and that is why it is stored rather than derived.**  The app
already records, permanently, which bank line became which purchase in which
budget line -- so *where has Amazon gone before?* is answerable with a join and
no table at all.  That was considered and rejected on 2026-08-19 (developer),
for three reasons and the third is the one that settles it:

  * where a merchant's money went last April is EVIDENCE; where it should go
    from now on is a decision only the owner can make, and the two are
    different facts.  A stored decision duplicates nothing -- it is derivable
    from no other row in the database -- so none of the normalization
    arguments against a stored derivation apply to it;
  * history changes underneath a derivation.  Delete an old purchase or
    release an old match and the app's forward-looking answer moves, without
    anyone deciding it should;
  * **history cannot express "never".**  A line the owner deliberately left
    alone leaves no trace at all, so a derivation is silent about exactly the
    merchant that matters most.  Measured on the developer's own statement:
    Capital One Credit Card is 9 of the 91 unexplained outflows and
    **`-$7,412.94` of the `-$11,336.36` in that list**, and every one of them
    must NEVER become a purchase -- the app already holds that money as CC
    Payback rows, so recording it would count it twice.  Without this table the
    screen re-asks those 9 questions on every pass and there is nowhere to
    write down the answer.

**The same shape ruling R-FP already gives the account identity**: recorded by
the user's own choice and then CHECKED, never inferred.  Nothing here writes
money and nothing here can: a policy is read to SUGGEST, and the only thing
that records a purchase is an explicit destination on one specific line
(developer ruling, 2026-08-19; see :class:`MerchantDestination`).

**A policy is restated and withdrawn freely, and is expected to be.**  It is a
statement about today's shape of the budget, not a judgement: when the credit
card arc gives Capital One its own account, the Checking-side line stops being
"not a purchase" and becomes a payment to MATCH against the card's payback row,
and the card account gets policies of its own -- which is why the key carries
the account.
"""

from app.extensions import db
from app.models.mixins import (
    AccountScopedMixin,
    TimestampMixin,
    UserScopedMixin,
)


class MerchantDestination(AccountScopedMixin, UserScopedMixin, TimestampMixin,
                          db.Model):
    """Where this owner has said one merchant's spending goes on one account.

    **THREE ANSWERS, and they are the complete set rather than a menu.**  A
    budget line either has a period-independent identity or it does not: a
    recurring one is generated from a ``budget.transaction_templates`` row and
    that template IS its identity across every period, while an ad-hoc one
    exists in a single period and nowhere else.  So *where does this merchant
    go* can be answered in exactly three ways:

    1. **a TEMPLATE** -- file into whatever row that template generated in the
       line's OWN pay period;
    2. **a NEW ENVELOPE** -- create one for the line's period, with this name
       and this category;
    3. **never a purchase** -- all three columns NULL.

    A fourth state is the absence of a row: the owner has not said.  It is
    distinct from (3) and the screen says so differently, which is the whole
    point of storing (3) at all.

    **Why not the envelope, and why not its NAME.**  Measured on the
    developer's own data 2026-08-19: the 24 unexplained Amazon lines fall in
    **10 different pay periods**, so there is no single ``transactions`` row to
    remember -- an envelope belongs to one period.  And the name is not stable
    either: template 22 generated a row called ``Kayla`` in one period and
    ``Kayla's Spending Money`` in the other 60, so a rule matching on the name
    would silently miss that period where one naming the template does not.
    IDs for logic, strings for display, one more time.

    Columns:
        account_id -- the account whose statements this governs
            (:class:`~app.models.mixins.AccountScopedMixin`).  **Per account
            and not per owner**, because a destination is: a Checking policy
            naming a Checking template is meaningless on a card statement, and
            the same merchant legitimately goes somewhere else there.
        user_id -- its owner (:class:`~app.models.mixins.UserScopedMixin`),
            held equal to the account's by ``fk_merchant_destinations_owner``
            so it is a co-located key rather than a copy.  The same
            construction ``fk_account_external_identities_owner`` uses.
        merchant -- what the BANK names the merchant
            (``bank_statement_lines.merchant``), verbatim.  **Not case-folded
            and not normalized**: two spellings from one bank are two policies,
            which is honest -- deciding that ``Amazon`` and ``AMAZON`` are one
            merchant is a guess, and this table exists not to make guesses.
            Measured: the developer's 361 lines carry 59 distinct merchants and
            **0 pairs differing only by case**.
        template_id -- answer (1), else NULL.
        envelope_name / category_id -- answer (2), else both NULL.
        created_at / updated_at -- when it was first stated and last restated
            (:class:`~app.models.mixins.TimestampMixin`).  A policy is EDITED
            in place rather than superseded by a new row, because it answers
            one question and the answer is whatever the owner last said; the
            history of what they said before is
            ``system.audit_log``'s, which this table has a trigger for.

    **Every subject key CASCADES, and the consequence is deliberate.**  Deleting
    a template or a category takes the policy with it, leaving the merchant
    unanswered -- which is the truth, because an answer naming a row that no
    longer exists is not an answer.  ``RESTRICT`` would refuse an ordinary
    delete because of a PREFERENCE the user cannot see from the thing they are
    deleting, which is the dead end finding **N-302** records one arc over.
    """

    __tablename__ = "merchant_destinations"
    __table_args__ = (
        # ONE answer per merchant per account.  The key is what makes
        # "restating a policy" an UPDATE rather than a second row that would
        # leave two answers to one question.
        db.UniqueConstraint(
            "user_id", "account_id", "merchant",
            name="uq_merchant_destinations_owner_account_merchant",
        ),
        # THE THREE SHAPES, spelled as three shapes.  A count-the-NULLs form
        # (``ck_statement_match_members_one_subject``'s) cannot say this:
        # answer (2) sets TWO columns and answer (3) sets none, so what has to
        # be constrained is which COMBINATIONS are legal rather than how many
        # columns are filled.
        db.CheckConstraint(
            "(template_id IS NOT NULL AND envelope_name IS NULL "
            "AND category_id IS NULL) "
            "OR (template_id IS NULL AND envelope_name IS NOT NULL "
            "AND category_id IS NOT NULL) "
            "OR (template_id IS NULL AND envelope_name IS NULL "
            "AND category_id IS NULL)",
            name="ck_merchant_destinations_one_answer",
        ),
        # A merchant is a NAME or this row keys nothing.  The same rule
        # ``ck_bank_statement_lines_merchant_not_blank`` states on the column
        # this one joins to, so a blank cannot enter from either side.
        db.CheckConstraint(
            "btrim(merchant) <> ''",
            name="ck_merchant_destinations_merchant_not_blank",
        ),
        # An envelope NAME is a name too, for the same reason
        # ``transactions.name`` is NOT NULL: answer (2) creates a budget line
        # with it.
        db.CheckConstraint(
            "envelope_name IS NULL OR btrim(envelope_name) <> ''",
            name="ck_merchant_destinations_envelope_name_not_blank",
        ),
        # This row's owner IS its account's, guaranteed rather than maintained
        # -- keyed onto ``uq_accounts_id_user``, the construction
        # ``fk_account_external_identities_owner`` uses one table over.
        db.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_merchant_destinations_owner",
            ondelete="CASCADE",
        ),
        # ...and the TEMPLATE it names is on that same account, structurally.
        # A statement is one bank's record of ONE account, so a policy pointing
        # at another account's recurring envelope is not a destination at all.
        # Composite rather than a bare ``template_id`` FK for the reason
        # ``fk_statement_match_members_transaction_account`` is composite:
        # otherwise "is this template on this account" is a reader's check that
        # can be forgotten, and the row it protects is one a crafted request
        # reaches.  ``MATCH SIMPLE`` (PostgreSQL's default) is what lets it sit
        # beside the nullable arms -- a row whose ``template_id`` is NULL
        # satisfies it whatever ``account_id`` says.
        db.ForeignKeyConstraint(
            ["template_id", "account_id"],
            ["budget.transaction_templates.id",
             "budget.transaction_templates.account_id"],
            name="fk_merchant_destinations_template_account",
            ondelete="CASCADE",
        ),
        # ...and the CATEGORY it names is this owner's.  Categories carry only
        # a ``user_id``, so that is the whole of their ownership fact -- and a
        # foreign ``category_id`` satisfies a bare FK perfectly well, which is
        # the IDOR every create door in this project probes for by hand.  Here
        # it is unwritable instead.
        db.ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["budget.categories.id", "budget.categories.user_id"],
            name="fk_merchant_destinations_category_owner",
            ondelete="CASCADE",
        ),
        # The review screen reads a whole account's policies at once.
        db.Index("idx_merchant_destinations_account", "account_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # 100 to match ``bank_statement_lines.merchant``, which is what it joins
    # to: a key stored narrower than its source would silently fail to match
    # the longest merchants.
    merchant = db.Column(db.String(100), nullable=False)
    # No direct single-column keys on either arm: both are reached through a
    # composite key that also holds the owner or the account equal.  Same
    # shape, same reason, as ``statement_match_members``' three subject keys.
    template_id = db.Column(db.Integer)
    envelope_name = db.Column(db.String(200))
    category_id = db.Column(db.Integer)

    # **No relationships, deliberately.**  Both arms are reached through a
    # COMPOSITE key whose other half comes from a mixin, so a relationship here
    # would have to name its foreign keys as strings and would then lazy-load
    # one row per policy on a screen that renders every merchant on the
    # account.  ``statement_match._policy`` loads the templates and categories
    # it needs in one statement each, which is the same shape
    # ``_reads._by_id`` already uses for a match's members.

    def __repr__(self):
        answer = (
            f"template={self.template_id}"
            if self.template_id is not None
            else f"new={self.envelope_name!r}"
            if self.envelope_name is not None
            else "never"
        )
        return (
            f"<MerchantDestination account={self.account_id} "
            f"{self.merchant!r} -> {answer}>"
        )
