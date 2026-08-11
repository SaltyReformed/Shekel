# Planning-document conventions

**One copy of the rules every arc's planning document is held to.** They lived in three places --
balance README Section 9, the recurrence plan's Section 7, the pay-calendar plan's Section 7 -- in
near-identical wording, which is the same denormalization the arcs keep finding in the code. The
credit-card plan had none at all and drifted furthest.

**Rules 1-4, 7 and 10-15 are PREDICATES**, graded by `tools/plan_gate/` through a pre-commit hook
scoped to these documents and the CI step that runs the custom pylint checkers -- so EDITING a
planning document is what runs the gate.
**Rules 5, 6's "replaced, never appended" half, 8 and 9 are DISCIPLINES.** Saying which is which is
the point: a safety that is not a predicate is not a safety, and labelling a discipline as one is
the failure being guarded against.

## The shape

| file | holds | kind |
|---|---|---|
| `ledger.md` | every open finding in every arc | registry |
| `steps.md` | every step IN EXECUTION ORDER, one line each, plus the unruled forks | registry |
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

**A step's SPECIFICATION is argument, not registry.** `steps.md` carries the ORDER and one sentence
per step; the 82 open specifications in one file would be ~1,150 lines, which is the master document
this structure exists to avoid.
**The sentence is not a stub of the specification, it is the whole answer to "what is this step"**
-- rule 14 grades it, because an index that answers that question with a fragment sends the reader
to a second document to learn what they came for, which is the cross-referencing this structure
exists to end.

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

3. **A registry that states its own size has that number CHECKED.** Three sentences carry five
   numbers and the gate grades all five: `ledger.md`'s `**The ledger stands at N rows.**`, and
   `steps.md`'s `**N steps, M open.**` and `holds N edges over M rows`. The balance ledger once read
   38 against a 40-row table because a step that closed four rows and opened three updated the rows
   and not the prose about them. **`steps.md`'s four went ungraded until 2026-08-11 and every one of
   them was stale by the time the arm was written**: "112 steps, 96 open" against 113 and 95, and
   "93 edges over 58 rows" against 94 and 59 -- wrong in both directions inside one merge, because
   one session appended a step while another ticked one. The rule had been written about one
   registry and enforced only there, so its sibling carried the exact defect the rule describes, and
   a cold reader was told what may start now by a number the gate had no opinion about. **A rule
   stated for one artifact and graded on one artifact is a rule the second artifact does not have.**

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
   **`steps.md`'s `commit` column is the same rule on the index**, and it went ungraded until
   2026-08-09: three of twelve SHIPPED rows held `--` while their own arc entries cited a hash, so
   the index said "shipped" and refused to say what shipped it. The two hashes need not be EQUAL --
   `X-aj1`'s cell names the first of its three commits and its entry opens with the merge -- because
   which commit is the useful one genuinely differs by step. What is graded is that each document
   names ONE. **The balance document's exemption from this rule is CLOSED**: it was justified by a
   count that had gone stale in both directions, which is what a disabled arm does to the claim it
   rests on.

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

11. **An identity class shares a tick state, and a fork binds both its remedies and its row.** `C2`,
    `X-l` and `R-F12` are ONE step under three names; ticking one without the others is a failure.
    Where two steps in different arcs are COMPETING remedies for one defect, the gate refuses a tick
    on either while the fork is unruled -- whichever ships first decides for both, and `P3` /
    `N-123` went unnoticed from April to 2026-08-09 for want of exactly this.
    **A fork is RULED only when its `ruled` cell NAMES one of its own remedies**, because every
    other spelling of "nobody has answered yet" -- `TBD`, `pending`, `?` -- used to read as ruled
    and turned the whole arm off. And **a ruled fork's defect row is owned by the remedy that won**:
    rule 2 re-points a row when its owner ships, but it cannot fire on a row owned by
    `developer-decision`, which is what an open fork's row carries. Since rule 1 resolves an owner
    within the row's own arc, ruling a fork can MOVE its row to the winner's arc.

12. **`steps.md` and the arc documents agree in both directions.** An index row with no
    specification in its arc document, and a specification with no index row, are both failures.

13. **`steps.md`'s `blocked by` cell is the dependency GRAPH, and it is graded.** The cell is `--`
    or a ` / `-separated list of `arc:id` keys, each optionally annotated in parentheses -- the same
    grammar the `aliases` cell uses, because both carry a list of step keys and two grammars for one
    shape is the denormalization these registries remove. Five arms: no step blocks itself; every
    key names a real step; a SHIPPED step is never blocked by an OPEN one; the graph is ACYCLIC; and
    an identity class shares ONE blocker set, for the reason rule 11 makes it share one tick state.
    **The acyclicity arm is why this rule exists.** "`R6` ships WITH `X-an`" was carried by three
    documents until 2026-08-09, when building `X-an`'s first leaf showed it unsatisfiable: `R6`
    reads a column `R5` creates, and `R5` waits on `X-f4`, three steps behind `X-an` with a
    moves-money PR between them. `steps.md` had recorded `R6 blocked by balance:X-an`, and nothing
    reconciled the two --
    **the column was parsed into `StepRow.blocked` and never read by any arm**, so every edge in it
    was decoration. An unsatisfiable ordering claim is not a scheduling preference; it is work that
    cannot be done in the order the plan states. **A DECOMPOSITION is NOT an edge in that column**
    -- rule 2 already puts it in the id -- but it is graded by a sixth arm:
    **a step that DECLARES itself "the DECOMPOSED parent" may not be SHIPPED while a leaf is open**,
    which is rule 2's own sentence made a predicate. The parent set is DECLARED and only the leaf
    set is derived, and that asymmetry is the design: deriving BOTH by id prefix claims `R-F1` as
    the parent of `R-F10`, `R-F12` and `R-F13`, three unrelated findings-steps, with `R-F1` shipped
    and all three open -- three false failures on the first run. Deriving NEITHER would need a list
    of parent names, which is finding `N-147`'s defect and what Phase G exists to delete.
    **A parent holding no leaves is silence, not a failure**: rule 5 archives completed spans,
    `X-f1`'s fourteen leaves have already left the index, and an arm that demanded they still be
    there would put rules 5 and 13 in contradiction.

14. **`steps.md` states the EXECUTION ORDER, and the order is graded against the graph it must
    obey.** Rule 13's column is a CONSTRAINT and it under-determines the answer: 38 open steps are
    legal to start at once, so the graph can say "any of these" and never "this one next". The
    sequence is therefore a DECISION -- taken from each arc's own stated sequencing -- and rule 13
    is what keeps that decision honest rather than what produces it.
    **The `order` cell has exactly three spellings**: `#N`, `container` and `SHIPPED`. Ranks are
    dense from 1, so "the first row that is not done" is always the next step; a rank repeats only
    across an IDENTITY CLASS, for the reason rule 11 makes one share a tick state; and a step is
    never ranked at or before an unshipped step it is blocked by, which is the arm that makes the
    sequence EXECUTABLE rather than merely written down. A container blocker resolves to the rank
    its last leaf holds. **A CONTAINER is not a position.** A decomposed parent is a name for a
    group, never a thing a reader picks up, so it leaves the order entirely and says which rank it
    ticks at instead. The previous index listed `X-f`, `X-aj`, `X-i` and `X-x` as though they were
    pickable work. **The `starts` cell is DERIVED and this rule is its reconciler**: `NOW` exactly
    when nothing unshipped blocks it, `after #N` naming the LATEST unshipped blocker's rank. Storing
    it at all is a deliberate exception to the rule against derived copies, and it is only legal
    because the gate recomputes it on every commit that touches the file -- an unreconciled copy is
    the root cause three of these arcs exist to remove. A stale `NOW` sends a reader at work they
    cannot start; a stale `after` hides work they can.
    **Every step's description is ONE COMPLETE SENTENCE within a cap.** Terminal punctuation is the
    predicate because truncation cannot fake it -- a cell cut at a character boundary ends in a
    letter, a comma or a backtick, never a full stop.
    **The class this arm exists for was 38 rows wide**: the index had been generated by taking the
    head of each arc entry, so rows ended `-- **THE`, `not an` and `the DECOMPOSED parent,`.

15. **A document under `archive/` or `historical/` is a HISTORICAL RECORD, it governs nothing, and
    it SAYS SO on its first line.** It may be cited for how something came to be; it may never be
    read as a live plan or as a statement of the current state, and the code as committed is the
    source of truth for what the app does. **The banner is on the ARTIFACT, not on the documents
    that might link to it, and that placement is the whole rule.** A session read
    `archive/implementation_plan_fail_loud_ledger_authority.md` as the plan of record; no live
    document pointed there, and no live document had to -- a grep, a glob or a half-remembered
    filename reaches an archived file without any index's help, and the archive is precisely where a
    filename that once meant something still resolves. A rule policing live citations would have
    been green while that happened. The banner is what a reader arriving from anywhere cannot get
    around.

## The two relations in `ledger.md`, and why conflating them deletes work

**This distinction is a PREDICATE, not prose.** It was prose until 2026-08-09, and rewriting every
`=` as `~` was then measured to change nothing anywhere -- while `N-128` sat pointing at
`recurrence:F-10`, a row a merge had already removed.

- **`= arc:id`** -- the SAME claim, recorded in another arc's ledger and MERGED into the row that
  carries the relation. Two rows worded differently cannot be combined mechanically, so each merge
  is a reviewed edit, and the absorbed id stays in the `also` column because the commit messages
  citing it cannot be edited.
- **`~ arc:id`** -- a DISTINCT finding sharing a root cause, usually cause and symptom, usually with
  **different owners and different closing steps**.

Five rows were claimed by their own documents to be "the same defect". Three survived a side-by-side
read of the row text and the owner column and were merged on 2026-08-09; two were cause and symptom
and were not. Merging those two would have collapsed two steps that each owe something into one
owner. **Never take a document's prose claim that two rows are the same defect -- compare the text
and the owners.**

**A merged row sits in the arc whose step closes it**, because rule 1 resolves an owner within the
row's own arc. Where that step is not yet decided the owner is `developer-decision` and the fork is
recorded in `steps.md`: naming either remedy would decide it.
