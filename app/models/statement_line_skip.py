"""
Shekel Budget App -- The SKIPPED bank line (budget schema)

One table, one subject (plan step ``bank_import:X-gj-4a``, ruling
**bank_import:R-JG**): a bank line the owner has decided explains nothing they
budget for.

**It is the FOURTH verb's act row.**  Ruling **R-HP** says every bank line ends
on exactly one of MATCH, ADD, TRANSFER or SKIP, and the Reconcile inbox is the
lines with none yet.  Three of the four already have a record --
:class:`~app.models.statement_match.StatementMatch` is what a match and a
recorded purchase both leave behind, and a bank line that BECOMES a transfer
will be matched against that transfer's own shadow row when the card arc ships
(finding **N-337**).  SKIP is the one verb that names no app row at all, and
that is exactly why it cannot reuse that table: :func:`~app.services
.statement_match._candidates.act_still_names_a_row` deliberately treats an act
with no app-side member as NOT A CLAIM, so a match holding a line and nothing
else leaves the line reading unexplained forever.  The disposition therefore
needs a store of its own.

**Why it is not a column on the line** (ruling **R-JG**).  Every column on
:class:`~app.models.statement_import.BankStatementLine` is a fact the SOURCE
stated, and the re-import path is written on that premise:
``statement_import._record._absorb_gained_facts`` fills any NULL column from
what a later export states.  A *the owner skipped this* column would be the
first column there with no source, and that loop would need a rule excluding it
-- a fence where a separate table needs none.  The line table also carries no
``user_id``, so who decided would be unrecordable.

**Why it is not append-only** (ruling **R-JG**).  Undoing a skip DELETES the
row, and the forensic record is kept by infrastructure that already exists:
this table joins :data:`app.audit_infrastructure.AUDITED_TABLES`, whose DELETE
arm writes ``to_jsonb(OLD)`` and the acting ``user_id`` into
``system.audit_log``.  An event log carrying an *unskipped* row would add only
a history the APP could display, which nothing asks for, at the price of a
"latest row per line" read on the pass and on the grid's badge count -- and of
a row whose meaning is *no disposition*, which is the shape
:class:`~app.services.statement_match.RuleAnswer` refuses in as many words.
It is the same shape :class:`~app.models.statement_match.StatementMatch`
already takes: *a match is never edited: correcting one is deleting it and
matching again*.

**A skip moves NO money, and that is what makes this leaf safe.**  It records a
decision about the WORK, not about the books: the bank's own record still shows
the line, the app still records nothing for it, and
:func:`~app.services.bank_agreement.bank_agreement`'s comparison still reports
the difference.  What changes is only that the Reconcile inbox stops asking.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


class StatementLineSkip(UserScopedMixin, CreatedAtMixin, db.Model):
    """One bank line the owner has decided is explained by nothing.

    Columns:
        bank_statement_line_id -- the line that was skipped.
        account_id -- the account it belongs to, held equal to the LINE's by
            ``fk_statement_line_skips_line_account`` and to the OWNER's by
            ``fk_statement_line_skips_owner``.  A co-located key rather than a
            copy a writer maintains, which is the construction
            ``bank_statement_lines.account_id`` and
            ``statement_match_members.account_id`` both take.
        user_id -- who decided (:class:`~app.models.mixins.UserScopedMixin`),
            held equal to the account's owner by ``fk_statement_line_skips
            _owner`` so it is a co-located key rather than a copy a writer
            maintains -- the wording
            :class:`~app.models.statement_match.StatementMatch` uses for its
            own, and it makes no derivability claim.  *A first draft justified
            the column by citing ruling **R-GT** and calling the decider "not
            derivable from anything else here", which is false: the composite
            key leaves exactly one legal value for a given ``account_id``, one
            join away.  A false reason is worse than none, because the next
            table copies it.  Named by adversarial design review 2026-09-02.*
        created_at -- when (:class:`~app.models.mixins.CreatedAtMixin`).  A
            skip is never edited: changing your mind is deleting it, so there
            is no ``updated_at`` --
            :class:`~app.models.statement_match.StatementMatch`'s own rule,
            for its own reason.

    **It stores no reason, and that is the locked direction's shape rather than
    an omission.**  ``docs/design/bank_import_audit.md`` gives the Skipped tab
    "the same card with Undo" and no free text, and the panel's own sentence
    already says what skipping means.  A note nobody was asked for is the
    speculative field ``CLAUDE.md`` rule 13 forbids.

    **No ``applied_by_rule`` either**, and that absence is ruling **R-JH**: a
    standing *never a purchase* answer BARS THE CREATE DOOR and claims nothing
    more -- *not a purchase* is not *explained by nothing*, since a paycheck is
    neither -- so no rule files a skip and the column would be ``false`` on
    every row that can exist.  Adding one for a filer that does not exist is
    the same speculative shape.

    **There is deliberately no direct foreign key to ``budget.accounts``.**
    Both composite keys below reach it -- the line's, and the owner's -- which
    is the idiom :class:`~app.models.statement_import.BankStatementLine` states
    for its own ``account_id``: a second single-column key would be a third
    path to one fact.
    """

    __tablename__ = "statement_line_skips"
    __table_args__ = (
        # THE DISPOSITION IS ONE PER LINE, structurally rather than by a
        # writer checking first.  Total rather than partial, because the
        # column is NOT NULL: every row here is a claim about a line.  It is
        # what makes the skip door idempotent at the database tier -- a
        # double-submitted press cannot record the same decision twice.
        db.UniqueConstraint(
            "bank_statement_line_id",
            name="uq_statement_line_skips_line",
        ),
        # This skip's account IS its line's, and the skip goes when the line
        # does.  **CASCADE, where ``fk_statement_match_members_line_account``
        # deliberately takes NO ACTION**, and the asymmetry is the difference
        # between the two acts rather than an inconsistency: a MATCH that has
        # lost its bank lines still claims app rows, so it goes on reporting
        # them as explained while no screen can render or release it (measured
        # on a production clone 2026-08-20, plan step ``bank_import:X-f6a-4``).
        # A skip claims NOTHING but its line, so a skip with no line is not a
        # dangerous record -- it is no record at all, and refusing to remove
        # one would block the repair door ``delete_import`` exists to be.  What
        # that door owes instead is a COUNT: it reports ``skips_forgotten``
        # beside the matches it released, because a destructive act that says
        # only "done" leaves the owner unable to tell a no-op from a larger
        # removal than they meant.
        db.ForeignKeyConstraint(
            ["bank_statement_line_id", "account_id"],
            ["budget.bank_statement_lines.id",
             "budget.bank_statement_lines.account_id"],
            name="fk_statement_line_skips_line_account",
            ondelete="CASCADE",
        ),
        # This skip's owner IS its account's, guaranteed rather than
        # maintained -- the same construction ``fk_statement_matches_owner``
        # uses, against the same ``uq_accounts_id_user`` superkey.
        db.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_statement_line_skips_owner",
            ondelete="CASCADE",
        ),
        # The pass asks for one ACCOUNT's skipped lines, once per render, and
        # the grid's badge count asks the same question in a subquery.
        db.Index("idx_statement_line_skips_account", "account_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # No direct single-column keys: the one relationship this row has is
    # reached through a composite that also holds the account equal.  Same
    # shape, same reason, as ``statement_match_members.account_id``.
    bank_statement_line_id = db.Column(db.Integer, nullable=False)
    account_id = db.Column(db.Integer, nullable=False)

    # **THERE IS DELIBERATELY NO ``line`` RELATIONSHIP.**  One was written and
    # removed before this shipped: nothing reads it.  Both doors address the id
    # columns directly, and a relationship here would be an abstraction one
    # leaf ahead of its only reader, which is what ``CLAUDE.md`` rule 13
    # forbids.  Named by adversarial design review 2026-09-02.
    #
    # **The tab that renders a skipped line's card has SHIPPED, and it did not
    # need one** (plan step ``bank_import:X-gj-4c-2``).  This comment said
    # "when 4c needs it, it adds it"; :func:`~app.services.statement_match
    # ._skipping.skipped_acts` spells the two-term join in its own query
    # instead, which puts BOTH halves of the composite key in the join -- so
    # the account equality travels with it (finding **bank_import:N-358**)
    # exactly as a relationship would have, with no model surface added and
    # nothing to keep ``viewonly``.  The promise is recorded as KEPT by being
    # unnecessary rather than deleted silently.

    def __repr__(self):
        return (
            f"<StatementLineSkip line={self.bank_statement_line_id} "
            f"({self.id})>"
        )
