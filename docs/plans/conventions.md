# Planning-document conventions

**One copy of the rules every arc's planning document is held to.** They lived in three places --
balance README Section 9, the recurrence plan's Section 7, the pay-calendar plan's Section 7 -- in
near-identical wording, which is the same denormalization the arcs keep finding in the code. The
credit-card plan had none at all and drifted furthest.

**Rules 1-4, 7, 10, 11 and 12 are PREDICATES**, graded by `tools/plan_gate/` through a pre-commit
hook scoped to these documents and the CI step that runs the custom pylint checkers -- so EDITING a
planning document is what runs the gate.
**Rules 5, 6's "replaced, never appended" half, 8 and 9 are DISCIPLINES.** Saying which is which is
the point: a safety that is not a predicate is not a safety, and labelling a discipline as one is
the failure being guarded against.

## The shape

| file | holds | kind |
|---|---|---|
| `ledger.md` | every open finding in every arc | registry |
| `steps.md` | every step, one line each, plus the unruled forks | registry |
| `conventions.md` | this file | registry |
| `docs/audits/balance_architecture/README.md` | the balance arc's argument, rulings and step specifications | argument |
| `implementation_plan_recurrence_redesign.md` | the recurrence arc's | argument |
| `implementation_plan_pay_calendar.md` | the pay-calendar arc's | argument |
| `implementation_plan_credit_card.md` | the credit-card arc's | argument |

**Merge what shares KEYS; split what shares only a READER.** Findings and steps alias across arcs,
so they are one table each. A root cause, its evidence, its target model and its rejected
alternatives are an argument about ONE subject that shares no key with any other arc, so they stay
split -- and so do RULINGS, which are arc-local and whose three source tables have three different
shapes.

**A step's SPECIFICATION is argument, not registry.** `steps.md` is an index. The 82 open
specifications in one file would be ~1,150 lines, which is the master document this structure exists
to avoid.

## The rules

1. **Every `ledger.md` row names a LIVE owner.** The owner column is a ` / `-separated list, each
   entry an unticked `steps.md` id (optionally annotated in parentheses), or `operator` with the
   question stated, or `developer-decision` with the date the fork was taken.
   **There is deliberately no value meaning "someone will get to it".** Retired as values: "own
   commit", "own step", "own arc", "if ever", "recorded, deferred", "residue", and any wake
   condition -- they all mean nobody. A row with an empty owner, an owner naming no step, or an
   owner naming a TICKED step is a failure. **A finding is BORN with an owner**: the review or trace
   that records it assigns one in the SAME commit.

2. **A step that ships re-points every row that named it.** Ticking a box is the same edit as
   re-pointing its findings; the gate refuses the commit that does one without the other.
   **Step ids are append-only** -- a decomposition appends a suffix, and nothing is renumbered for
   readability, because ids are cited in commit messages, code comments and archived records.

3. **`ledger.md` states its own size and the number is checked**
   (`**The ledger stands at N rows.**`). The balance ledger once read 38 against a 40-row table
   because a step that closed four rows and opened three updated the rows and not the prose about
   them.

4. **Every document is capped, and a cap is a FORCING FUNCTION rather than a ceiling sized to fit
   the work.** Raising a cap is not the answer when it binds; rule 5 is. Current caps live in the
   gate's own constants.

5. **The only legal way back under a cap is to archive a COMPLETED span**, condensed to one line per
   step: its id, its commit and what it closed. Never trim a live step's specification to fit.
   **Shrink the record of what is DONE, never the specification of what remains.** Three conditions:
   unfinished work stays in `ledger.md` whichever arc it came from; no live sentence may depend on
   an archived one; and a row carried WITHOUT re-verification must say so.

6. **Each arc document's "where this stands" is a SIGNPOST, capped and REPLACED rather than appended
   to.** It names what just landed, what is in flight and what is next, and POINTS at the detail. On
   the balance README it once became an append-only log of 1,019 lines.
   **It may not store a volatile value**: branch state, whether one branch leads another, and what
   production is running are MEASUREMENTS, and the row names the command rather than a copy of its
   answer. A stored copy is a derived value beside no reconciler, which is the root cause several
   arcs here exist to remove.

7. **A SHIPPED step's specification is a POINTER: it OPENS with its commit hash.** The hash's
   POSITION is the predicate, not its presence -- an Alembic revision id is hex too. A LIVE step is
   a specification and is never trimmed.

8. **A finding is not deferred for cost.** "Materially larger than this step" is a reason to give
   something its OWN step, never a reason to leave it unowned. A finding costing `$0.00` on today's
   data is not resolved; it is a defect waiting for the data to change, and the data changes without
   asking. Where a fix must follow another step to be decided correctly it is SEQUENCED behind it
   with the reason stated -- which is a schedule, and is what a deferral is not.

9. **A ruling is recorded as the RULE and its date, one line, in its own arc's document.** The
   deliberation belongs in the commit that ships it. Rulings are arc-local: no ruling has ever
   aliased across arcs, which is why they are the one registry-shaped thing that stays split.

10. **The arc is a COLUMN, never a prefix.** The key is `(arc, id)` and it is unique across the
    corpus. Bare ids keep their exact spelling, because a rename would orphan every citation in
    commit messages (immutable), code comments and the archived as-built records.
    **Where a bare id is ambiguous the citation must name its arc**: `D4` alone names three
    different findings -- `loan_arc_as_built_2026-07-26.md:564`,
    `implementation_plan_posting_ledger_loan_payments.md:353` and the recurrence arc's live row.

11. **An identity class shares a tick state, and an unruled fork refuses both.** `C2`, `X-l` and
    `R-F12` are ONE step under three names; ticking one without the others is a failure. Where two
    steps in different arcs are COMPETING remedies for one defect, the gate refuses a tick on either
    while the fork is unruled -- whichever ships first decides for both, and `P3` / `N-123` went
    unnoticed from April to 2026-08-09 for want of exactly this.

12. **`steps.md` and the arc documents agree in both directions.** An index row with no
    specification in its arc document, and a specification with no index row, are both failures.

## The two relations in `ledger.md`, and why conflating them deletes work

- **`= arc:id`** -- the SAME claim recorded in another arc's ledger. A merge CANDIDATE, not a merge:
  two rows worded differently cannot be combined mechanically, so the merge is a reviewed edit.
- **`~ arc:id`** -- a DISTINCT finding sharing a root cause, usually cause and symptom, usually with
  **different owners and different closing steps**.

Five rows were claimed by their own documents to be "the same defect". Three survived a side-by-side
read of the row text and the owner column; two were cause and symptom. Merging those two would have
collapsed two steps that each owe something into one owner. **Never take a document's prose claim
that two rows are the same defect -- compare the text and the owners.**
