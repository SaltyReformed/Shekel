# Implementation Plan: Recurrence Rule Redesign

## Where this stands

**Plan of record** for the two-axis recurrence model and the cash-date / installment-date split.
R1-R4 and the R7c cutover are ARCHIVED; the closed pattern set is GONE, which is what this arc was
for (R-R16 / R-R18 / R-R27). Which steps are in PRODUCTION is a measurement, never a stored value:
`git branch -r --contains <hash>` against `origin/main`.

**R7d DECOMPOSED into seven leaves 2026-08-25 (R-R33, R-R34) and R7d-c into two more 2026-08-27
(R-R38); three have shipped.** R7d-a prices an uncovered installment from the DEFINITION, R7d-b
built the resolver, R7d-c-1 got the read pass to generation; what is left moves readers onto the
resolver and R7d-g stops the WRITE. **A tie-break is a sign the SEARCH is the wrong question**
(R-R35): only ONE tier of three asks "which transfer into a loan is its payment", and **R16**
deletes the rest -- DECOMPOSED into four leaves 2026-08-26 (**R-R36**) once the fold turned out to
charge a month of interest per payment RECORD.

**What to do next is `steps.md`'s order table; do not re-derive it here.** One ruling is owed and
section 0 states its two options. Section 4 is the steps; the findings, the index, the rules and
`verification.md` are the shared registries in `docs/plans/`.

## The rulings

**This arc's rulings are in `rulings.md`, rows whose `arc` is `recurrence`.** The key is `(arc, id)`
and no arc document states a ruling; cite one as `recurrence:R-Rnn` wherever the bare id could be
another arc's (`conventions.md` rules 9 and 10).

**Ten decisions were archived on 2026-08-08** to `historical/recurrence_as_built_2026-08-08.md`: the
`Once` retirement, R2 and R4 sequencing, the wrong stored paycheck, bound semantics, the
`Monthly First` and period-unit anchors, write-door enforcement, and the two `R-R12` superseded.
That sentence was a table ROW until `balance:X-ao-2a` -- a section LABEL inside a registry, which is
finding **balance:N-372** and is why it is prose here rather than a row there.

---

## 0. Why this arc splits, and the one ruling still owed

**The ORDER is `steps.md`'s and is not restated here.** This section holds the one question the
split's measurement could not answer; the measurement itself, and the "R6 ships with X-an"
contradiction it exposed, are in `historical/recurrence_evidence_2026-08-11.md` (rule 5, 2026-08-26)
now that rule 13's graded blocker column makes the contradiction unconstructible.

**What the measurement said, and it is unchanged.** The ENGINE CORE (R1-R4) touches no file the
balance arc's anchor half edits, so it constrains nothing there. The DATE work does: `R5` and `R6`
sit on all four of `X-an`'s surfaces and one file of `X-f4`'s deletion set, which is why they are a
separate half and why the index gates them where it does. This arc asks which date IS the
contractual installment while X-an asks which date decides a payment already HAPPENED.

**`developer-decision` OWED, and it is the one thing here the index cannot settle.** Two options:
re-point R6 behind R5, which is what `steps.md` currently records; or split off the half that needs
no `due_on` -- the single `loan_installment_date` accessor over the rule -- and ship that beside the
remaining X-an leaf. **The index recording the first option is not the developer choosing it.**

**Consequence for Half A:** it must leave the `due_date` contract byte-identical so the R1 oracle
stays green, so no step before R5 touches the column. The transaction-template form's live "Due Day
of Month" field (`_recurrence_fields.html:104-111`, `routes/templates.py:472,649`) stays exactly as
it is until R5 gives the installment a column of its own.

---

## 1. Root cause

**Archived to `historical/recurrence_evidence_2026-08-11.md` on 2026-08-26**, beside section 2's
evidence and for the same reason: the closed pattern set, the wide sparse table and the reverse
`_match_*` generation are the state this arc DELETED (R7c-a..R7c-c, rulings R-R16 / R-R18 / R-R27),
and the section still described them in the PRESENT tense with five citations into a module that is
now a package. **The one-line root cause that survives**: one cadence family got a knob and the
other got the interval baked into an enum NAME, so "every other month" had nowhere to live -- which
is a missing AXIS, never a missing enum member. Section 3 states what replaced it.

## 2. Evidence

**Archived to `historical/recurrence_evidence_2026-08-11.md`.** The measurements and the rejected
options are a HISTORICAL RECORD: the rulings above state what was decided and the code states what
was built. Cite the archive for how a decision came to be, never for what is true now.

## 3. Target model

**This is the END state. Which step creates each piece is marked; see R2.**

```sql
ref.recurrence_units        -- PERIOD, WEEK, MONTH, YEAR                    [R2a, DONE]
ref.period_placements       -- CONTAINING_DATE, PERIOD_STARTING_ON_OR_AFTER [R2a, DONE]
ref.business_day_shifts     -- NONE, PRIOR, NEXT                            [R2a, DONE]

budget.recurrence_rules
  id               PK
  user_id          FK auth.users CASCADE          NOT NULL
  interval_n       INT   NOT NULL  CHECK (interval_n > 0)
  -- All five were COMPUTED until R7c and are authored columns since; the
  -- expand / backfill / tighten it took is in that step's own archive.
  unit_id          FK ref.recurrence_units RESTRICT   NOT NULL           [R7c-b]
  starts_on        DATE  NOT NULL   -- the rule's FIRST OCCURRENCE.  ONE
                                    -- meaning for every unit, and its
                                    -- position in the cycle IS the phase,
                                    -- so no month or day column survives
                                    -- beside it.  R-R16               [R7c-a]
  nominal_day      SMALLINT NULL  CHECK (nominal_day IS NULL OR (
                     nominal_day BETWEEN 29 AND 31
                     AND nominal_day > EXTRACT(day FROM starts_on)))
                                    -- the day the rule MEANS when
                                    -- starts_on's own month was too short
                                    -- to hold it.  29-31, not 1-31: a day
                                    -- the date already carries would be a
                                    -- second statement of it.  R-R3 [R7c-a]
  placement_id     FK ref.period_placements RESTRICT  NOT NULL           [R7c-b]
  shift_id         FK ref.business_day_shifts RESTRICT NOT NULL          [R7c-b]
  end_date         DATE  NULL   CHECK (end_date IS NULL OR end_date >= starts_on)  [CHECK at R7c-b]
  max_occurrences  INT   NULL   CHECK (max_occurrences IS NULL OR max_occurrences > 0)  [R2b, DONE]
  created_at
  CHECK (end_date IS NULL OR max_occurrences IS NULL)   -- at most one end bound

-- ONE subtype survives R-R13.  It carries a surrogate ``id`` PK plus
-- ``UNIQUE (recurrence_rule_id)``, NOT ``recurrence_rule_id`` as the PK: it is
-- audited, and ``system.audit_trigger_func`` assigns ``v_row_id := NEW.id`` --
-- on a table without that column every INSERT dies with
-- ``record "new" has no field "id"`` (measured on a probe table, R2b).
-- UNIQUE over a NOT NULL column enforces the identical 0-or-1 cardinality.

budget.recurrence_weekday_anchors    [R2b created it, EMPTY; R8 is the first writer]
                                     -- 0..1 per rule; nth-weekday-of-month rules.
                                     -- A REAL subtype: two fields with their own
                                     -- domain, not a repair for a lossy encoding.
  id                  PK
  recurrence_rule_id  FK -> budget.recurrence_rules ON DELETE CASCADE, UNIQUE
  nth_week            INT NOT NULL
                      CHECK (nth_week BETWEEN -1 AND 5 AND nth_week <> 0)  -- -1 = last
  weekday             INT NOT NULL CHECK (weekday BETWEEN 0 AND 6)  -- date.weekday(), 0=Mon

-- DROPPED by R-R13, unwritten: budget.recurrence_month_anchors.  Its whole
-- content was "the day I actually meant", present iff a DATE anchor had lost
-- it; ``nominal_day`` above holds it once instead.
-- NEVER CREATED (R-R12): budget.recurrence_due_dates.  The installment is a
-- fact about a generated ROW (``transactions.due_on``), where the loan ledger
-- already reads it, not about the rule.

budget.transactions / budget.transfers          [R5, ruling R-R12]
  occurs_on        DATE  NOT NULL   -- the date the CADENCE names
  pay_period_id    FK                -- the funding.  Already exists.
  due_on           DATE  NULL        -- the contractual installment, when it
                                     -- differs.  A POSTING INPUT.
  UNIQUE (template_id, scenario_id, occurs_on) WHERE ...   -- re-keyed off the paycheck
```

**`end_date >= starts_on` lands at R7c, with the column it names.** `end_date` is user-authored and
live; 14 live rules RESOLVE to a bound in the future, so setting an earlier end date -- exactly what
the field invites -- would become a `CheckViolation` out of `update_template`'s autoflush, which
nothing catches: the user could not stop an annual bill and the projection would keep charging it.
R7c adds it together with the Marshmallow validator that refuses the pair at the door.

**Zero conditionally-meaningless columns, and since R-R13 zero conditionally-MEANING-SHIFTING
ones.** `nominal_day` is NULL exactly when the unit does not fire on a day of the month, so absence
is the discriminator and no second table repairs a lossy one.

`placement` is well-defined for all four units, and **INERT under the PERIOD unit** -- a claim this
document retired and R3 restored by measurement. The retirement read `anchor_date` as the emitted
occurrence; R3 does not emit it. A pay-period-space rule emits the qualifying PAYCHECK's own
`start_date`, and both placements carry a period start back to that same period. Emitting the payday
is also what reproduces the current row's DATE: `compute_due_date` returns `period.start_date` for a
day-less rule, so emitting a mid-period bound would move it.

**The axis is short one value, and it is NOT the one this paragraph used to name.** It said both
members fund on or AFTER the occurrence, so a bill that must be funded in ADVANCE is inexpressible,
and named "the last paycheck on or before the occurrence" as the missing member (ledger row D20).
Plan step R8-a measured both halves false. `CONTAINING_DATE` funds from the paycheck whose span
COVERS the occurrence, so its payday is on or before that date by construction, and the member D20
named is that same rule under another name on a calendar whose periods tile.
**D20 CLOSED on that measurement.** What is genuinely inexpressible is a LEAD: funding from a
paycheck EARLIER than the containing one, so rent due 1 August is paid from the 17 July paycheck
rather than the 31 July one. Ledger row **D40**, plan step **R11**, which opens with the ruling of
what the lead is measured in.

**Only the `budget` tables are audited.** An earlier draft of this section said "all three new
tables go into `AUDITED_TABLES`"; measured against that list's own inclusion criteria
(`app/audit_infrastructure.py:46-64`) that is wrong for the `ref` tables -- the `ref` schema is
excluded with exactly one exception, the multi-tenant `ref.account_types`, and adding read-only seed
catalogues would both drown the trail in seed noise and move `EXPECTED_TRIGGER_COUNT`, which the
container entrypoint asserts at start. So: `ref.recurrence_units` / `ref.period_placements` /
`ref.business_day_shifts` are NOT audited (pinned by `TestNotAudited` in
`tests/test_models/test_recurrence_ref_tables_migration.py`), while
`budget.recurrence_weekday_anchors` and `budget.recurrence_month_anchors` went into `AUDITED_TABLES`
in R2b. **R7c drops the second of those with its table (R-R13), so it leaves the list in the same
migration** -- and `EXPECTED_TRIGGER_COUNT` moves DOWN by one, which the container entrypoint
asserts at start and which R7c must therefore update in the same commit.

### Where the old columns went

| old | new |
|---|---|
| `day_of_month` | `starts_on.day`, plus `nominal_day` on the one shape a short month loses (R-R16) |
| `month_of_year` | `starts_on.month` -- the FIRST OCCURRENCE's, which is in the cycle's residue class where the bound never was (R-R16, row D28) |
| `offset_periods` | `starts_on` (a date survives a schedule rebuild; an index does not) -- kills D1 |
| `start_period_id` (weak, bypassable) | deleted; `starts_on` is the start and is applied unconditionally -- kills D2 |
| `start_date` (strong, loan-sync only) | `starts_on` at R7c-c, which NARROWS D6 rather than closing it: for a loan rule that fires on a day of the month -- both live loans, and what `routes/loan/payment_transfer.py` sets up -- the first contractual installment IS an occurrence, so the fold is exact; for a DAY-LESS loan rule (row D27's unenforced precondition) `starts_on` is the payday of the paycheck that installment falls in, which selects the same paycheck and generates identically but does not keep the installment DATE. Nothing is lost that the app cannot re-derive: `rate_period_engine.first_installment_date(origination_date, payment_day)` answers it from the loan |
| `due_day_of_month` + implicit next-month rule | `transactions.due_on` / `transfers.due_on` on the generated ROW (R-R12) |
| `Once` pattern | deleted; `recurrence_rule_id IS NULL` for both template kinds |

### Generation becomes one function

**Built at R3.** The signature below is the shipped one, not the drafted one -- the draft read
`occurrences(rule, window)`, which cannot serve the PERIOD unit at all (finding D9, closed):

```text
occurrences(resolved, calendar, *, through) -> Iterator[date]      # forward, by unit
place(occurrence, calendar, placement) -> SchedulePeriod | None    # bisect, NOT total
occurrence_placements(resolved, calendar, *, through=None) -> tuple[...]
```

Forward generation plus placement is explicit -- a date the schedule cannot host is a stated "no
period" rather than one nobody looked for. This kills D3 structurally, at any cadence, and R4
retires all five `_match_*` helpers.

**It is not TOTAL, and the claim that it is was measured false.** This paragraph read "periods are
contiguous by construction (`pay_period_service.py:190`), so every date has exactly one period".
Contiguity holds WITHIN a generated batch and not across batches: `_reject_overlapping_batch` only
requires a new batch to start after the latest existing `end_date`, so `latest_end + 5 days` is
accepted and leaves a gap -- and registration bootstraps a 14-day period 0 that any later real
schedule starts after. `place()` answers "no period" and the composition REPORTS the unplaced
occurrence; generation LOGS it and skips it (ruled 2026-08-08, built at R4b-2, row D7 closed).
Closing the WRITER that permits a gapped batch is finding F-10.

## 4. Step sequence

Each step is a leaf boundary: one commit, its own tests green, independently revertible.
**Budget a neutral review pass and a fix pass into every one.** R2e-3 shipped at roughly twice its
specification because a review found that retiring a value is not done when nothing reads it -- it
is done when the SHAPE replacing it behaves -- and three further findings were false claims in this
arc's own new prose. **A step's behaviour change is stated by measurement, not by a hedge**: R4a's
draft entry claimed it changed nothing at the developer's cadence, and an adversarial review
disproved it -- the four `bounds.*` shapes that moved are on the BIWEEKLY schedule, and every live
loan-payment rule carries the `end_date` that moved them. Against production it moves nothing: 46
live rules, 866 generated rows, byte-identical under both engines. Half A = R1-R4, R7a-1 (archived,
`historical/recurrence_completed_span_as_built_2026-08-27.md`) through R7c, R8; Half B = R5, R6
(section 0).

**Any step that changes the recurrence form's controls runs
`tests/manual/verify_recurrence_form.py`.** A standing mandate for this arc, hoisted here at R7c-c
when the R7c entry that carried it was archived -- it binds steps that have not been written yet, so
it may not live inside a shipped step's pointer. Two of R7b-2's six defects were invisible to pytest
by construction: a control hidden by a class, an option hidden by a script, and a style the browser
REFUSED to apply all look identical in rendered HTML. R7c-c added a third kind -- the run found two
of the harness's OWN checks reading a database the app under test never wrote to, so they would have
passed with every refusal accepted.
**When the dev app is pointed at a database COPY, point the harness there too**
(`VERIFY_DEV_DATABASE`); the `ref` ids a `TEMPLATE` copy carries are identical either way, which is
exactly what made the mismatch invisible.

- [x] **R17** `4e8b40b3` -- as built: `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R5 -- a generated row carries THREE dates, in three places.**

**RESPECIFIED by ruling R-R12** (2026-08-08). The old specification -- rename `due_date` to
`occurs_on` as a pure rename, then read a `recurrence_due_dates` table -- rested on a premise
`ledger.md`'s **D4** now records as false, and is not buildable as written.

`occurs_on NOT NULL` is the date the CADENCE names, written from the `OccurrencePlacement` the
engine already computes and `resolve_generation_plan` already carries to the write loop.
`pay_period_id` is the funding and already exists. `due_on NULL` is the contractual installment,
present only when it differs. **`compute_due_date` is DELETED** -- it is the last reader of the
endpoint-month scan R4a deleted from period selection (row D18) and the last place the disproved
`due_dom < dom` next-month inference lives (R-R2). Two things follow that the old specification did
not have: the write loop stops discarding `PlannedOccurrence.occurrence`
(`recurrence_engine.py:342`, `transfer_recurrence.py:127`, the two producers of one fact), and
**the index re-key this step used to carry SHIPPED at `R17`**, with the
`RecurrenceCadenceUnsupported` retirement that rode on it. What remains here is the DATE split:
`compute_due_date` reads a row's PERIOD, so two occurrences inside one paycheck -- storable since
that re-key -- take the same due date, which is **D18**'s third door.

**It is a value-SPLITTING migration, not `alter_column ... new_column_name`**, and it is
destructive: it carries the `Review:` line and a refusing downgrade. The split is per row class -- a
loan payment shadow's stored value moves to `due_on` (it is the installment the ledger reads,
`models/transfer.py:169-177`), every other row's stays as `occurs_on`.
**`due_date` is a POSTING INPUT**, so the migration is followed by
`loan_posting_service.backfill_all_loan_postings()`, the caveat `c4e91a7b2d38` already carries. Own
PR. It also deletes a false claim: `compute_due_date`'s docstring names a "due-date backfill script"
that no longer exists anywhere in `scripts/`. Scope, re-measured 2026-08-08 rather than inherited:
**20 Python files touch the column in code**, 6 more name it only in prose, and 4 templates render
it (two carrying `<input name="due_date">`, so the wire format moves too). The plan's four
"highest-risk readers" were wrong about three of them -- `balance_at/_plan.py`,
`rate_period_engine.py` and `loan_payment_service.py` hold **zero** column references between them
and are R6 surfaces.

**It must also re-examine `build_transient_rule`**, carried here from `R-F6`'s entry when that step
was archived (2026-08-19, `conventions.md` rule 4: an overflow's destination is the OWNING step):
its last callers are tests needing a rule only because `compute_due_date` takes one, and this step
deletes that function.

- [ ] **R6 -- Delete `payment_day`; one installment accessor.**

`loan_installment_date(...)` becomes the single derivation over the rule plus `due_on`.
**There is no `recurrence_due_dates` table and there will not be**: R-R12 puts the installment on
the ROW, where the ledger already reads it, rather than on the rule. 22 files carry `payment_day` (3
doc-only, 2 the definition surface), and **19 of 22 already read it as the installment** -- exactly
two make it a CASH day, `routes/loan/payment_transfer.py:175` and `loan_recurrence_sync.py:172`, and
those two ARE D4's mechanism. Eight distinct producers of "when is this installment due" collapse
into one; the plan previously counted them as one accessor plus a rule read. Kills D4.
**This step needs its own review pass** -- it is the deepest cut into the ledger.

**R7 is THREE leaves**, ruled 2026-08-07: the cutover is the only irreversible-ish one, so the label
and form work is not carried into it.

**The four R7b leaves and R7c's first two LEFT this list at plan step R8-a** (`conventions.md` rule
5, one line per step: its id, its commit and what it closed). All six shipped; the first four are
accounted for in `historical/recurrence_as_built_2026-08-14.md` and the last two in
`historical/recurrence_findings_as_built_2026-08-15.md`, which R7c-c's own entry says to read first.

| step | commit | what it did, and what it closed |
|---|---|---|
| `R7b-1` | `e7eb3b1a` | The authored vocabulary becomes the two axes; the closed set becomes a storage ENCODING. Closed nothing |
| `R7b-2` | `ecc4d01b` | The form authors that vocabulary, its offer set derived from the encoder's own table. Closed **D8**; opened **D31**, **D32** |
| `R7b-3` | `c8655584` | One "Ends" control for a bound with three shapes, so a rule cannot state two. Closed **D23**; took the count-bounded end off **R8** |
| `R7b-4` | `67f013c8` | The opening bound becomes a DATE and the `Every N Periods` phase a derivation of it. Closed **D2**, **D30** |
| `R7c-a` | `370a30cc` (migration `f2a94c7e1b60`) | The two-axis columns land NULLABLE, backfilled and dual-written, read by nobody. Closed **D12** |
| `R7c-b` | `900e761a` (migration `b6d41f0a9c27`) | Every reader and the form move onto them; four columns tighten to NOT NULL. Closed **D10**, **D21**, **D24**, **D28**, **D31** |

- [x] **R7c** `ee35bca7` -- as built: `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R7c-c** `ee35bca7` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R7d-a** `89cb0c1d` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R7d -- a loan payment's CLOSING bound stops being a stored column.** DECOMPOSED 2026-08-25
      into seven leaves, one per surface that READS the column, after a census found this
      specification had enumerated only the ten WRITERS.

`loan_recurrence_sync` becomes a RESOLVER -- `loan_payment_window(template, ctx)` answering
`ClosesOn`, `Indefinite` or `EMPTY` -- and every reader asks it, instead of ten call sites writing
it into `budget.recurrence_rules` and hoping no reader gets there first.
**It takes the DEFINITION and not the loan** (ruling **R-R35**, which supersedes the
`(account, ctx)` this section specified). **R-R29 narrowed this to the CLOSING bound**; `starts_on`
stays stored. **Read TRAP 1: it cost this step a design.**

**The root cause is a CATEGORY ERROR in the table, and R7c-b is where it became visible.** Those two
columns hold two KINDS of fact: what a USER AUTHORS, where a stop before the start is a mistake to
report, and what the app DERIVES for a loan payment, where an EMPTY window is legitimate and
sometimes correct. `ck_recurrence_rules_valid_window` was drafted into R7c-b and HELD BACK on that
measurement (developer, 2026-08-15): originate 2026-08-01 with `payment_day` 1, then true the
balance to zero on 2026-08-15 -- the window is empty, forward generation emits nothing, and that is
right for a loan owing nothing, where a CHECK makes it an unhandled `CheckViolation` out of the
true-up. Both local repairs are worse: clamping to `max(as_of, starts_on)` admits ONE occurrence, so
a paid-off loan keeps a projected payment whose cash still debits while the fold books it to Refund;
and archiving the template inside a sync is destructive on a path that runs on every settle.

**The cached bound is STALE on live data, and it costs a budgeted payment.** Re-measured 2026-08-25
on a clone: rule 48 stores `end_date` `2029-01-22` where the Van's derived payoff is `2029-02-22`,
and extending the calendar by 26 periods generated rows only through `2029-01-22` -- the `$531.94`
installment due `2029-02-22` is never created.

**TRAP 1 -- DO NOT "fix" the ESTIMATED tier's future-only rule. It is finding B-9's FIX.**
`_estimated_from_contract` skips any contractual installment whose `payment_date < as_of`, so an
overdue slot with no record pays nothing. That reads like an oversight and is the opposite: the
retired forward walk amortized an installment per month whether or not one was recorded, and step
C6b deleted it for exactly that -- `-$15,755.38` per period. The LIVE statement is the comment on
the line itself. This step's first design read it as the root cause and proposed filling every
uncovered slot, which is B-9 re-introduced under a new name.

**TRAP 2 is CLOSED and ARCHIVED**, with the scope of R7d-a's invariance beside it, to
`historical/recurrence_r7d_trap2_as_built_2026-08-25.md`: a bound resolved before the rows exist is
NOT always later than the true one, measured twelve payments wrong through `regenerate_pay_periods`,
and `89cb0c1d` closed it. Row **N-352** carries what is left.

**R-R30 (2026-08-19) decides the FORM READ path and nothing else**: the create path already raises
and `period_population` already returns 0, so what an owner with no baseline changes is what a
locked "Ends" control renders. Its rule is in the Rulings index above; R7d-f applies it.

**The READER census forced the decomposition** (2026-08-25, by setting `end_date` NULL on both live
rules, then corrected by an adversarial review that found it wrong in BOTH directions). SEVEN
surfaces read a loan payment's closing bound where this specification named one, and each leaf below
names its own; the roll-call is archived to
`historical/recurrence_r7d_trap2_as_built_2026-08-25.md`. `_recurrence_preview` is NOT one -- it
composes from `request.args` and its control is `disabled` when locked, so it renders "never ends"
already. Ruling **R-R34**.

**What the resolver deletes.** Nine of the ten `sync_recurring_payment_bounds` call sites go
whole -- `params.py:330` / `:448`, `escrow_rates.py:170`, `payment_transfer.py:251` / `:277` /
`:344` / `:418`, `_loan_posting.py:306` / `:387` -- and with them the double sync at create, the
"idempotent WITHIN a day" caveat, and the stale bound D35 measures.
**The census is of the CLOSING bound only**: all ten also call `_sync_loan_cadence`, whose write
repairs three shapes a `payment_day` edit misses -- `create_params` calls no sync, a PAY-SCHEDULE
change moves a `PERIOD`-unit rule's resolved bound (**D39**'s shape), and a cadence-unit edit moves
`nominal_day`. After R7d the opening bound has THREE writers: `params.py:190`, `bind_rule_to_loan`
at `payment_transfer.py:250` and `transfers/_instances.py:222`. Decide what repairs those three
before deleting the path.

**`owns_validity_window` SPLITS; it is not deleted.** It is one predicate because
`_recurrence_form_refusals` states ONE writer owns both bounds -- a premise R-R29 makes false. It
drives the render lock and the refusal, so after R7d the "Ends" control either unlocks (a change to
a money-adjacent form) or stays locked for a value nothing stores. R7d-f decides which.

- [x] **R7d-b** `0462dc38` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R7d-c -- the DECOMPOSED parent of "generation takes the resolver."** Split into TWO leaves
      2026-08-27 (**R-R38**): the pass has to REACH generation first, and WHO opens it is a question
      about the three write doors, each of which did a write and then a read-dependent write in ONE
      call so no caller could get between them.

- [x] **R7d-c-1** `61d81c7f` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R7d-c-2 -- GENERATION takes the resolver.** Both engines' `resolve_generation_plan` applies
      `loan_payment_window`'s answer over the rule's own bound. **MOVES MONEY**: today it creates
      the Van's `$531.94` installment due `2029-02-22` the stale column drops. Carries **D46**.

**A first build is HELD at `9aff7ab9`** (`feat/r7d-c-2-loan-bound-at-generation`, no PR), recorded
because nothing else in `docs/` references it. Its `_plan.py` half is superseded by `R7d-d`'s
`Closing`; its 868 lines of test and harness are not, nor is the measurement `balance:X-au-f` rests
on -- Van Loan, 24 projected rows, stamped clone 2026-08-31: unedited `2029-02-22`, rows raised to
`$900.00` `2028-02-22`, halved to `$265.97` `2030-04-22`. The rows bound the generator.

- [ ] **R7d-h -- a loan gets ONE closing date, past AND future.**
      *(Id append-only per conventions rule 2; its RANK is before `R7d-d`, which it blocks.)*

`balance_at.loan_payoff_date` answers HALF its own question: it folds FORWARD from the confirmed
present seed, so a loan already at zero has no crossing left to date and returns `None` -- which
does not mean *never*, it means *not my half*. Three consumers each invent a meaning --
`recurrence_end_date` substitutes the READ CLOCK, `LoanFigures` adds `is_retired`, the equity chart
and `/savings` drop the loan -- rule 14's derived half broken by a producer refusing half its
domain, with each coping strategy an epicycle around a walk nobody wrote.

**The date IS derivable today, which is what the step rests on.** `walk_loan_ledger` ->
`dated_deltas` already produces the dated recorded balance and `balance:X-au-g-2c-3b-2` (`3b7716f8`)
put both tiers on `replay_loan_events`, so the backward crossing is `plan_payoff_date`'s forward
rule read the other way. Production clone at head, Van Loan trued to `$0.00`, read `2026-12-25`:
`payoff_date=None`, `is_retired=True`, recorded balance folds to `<= 0` on **`2026-09-01`** over
nine dated movements.

`loan_closing_date(account, ctx) -> date | None` -- closed at `as_of`, the day it LAST became
closed; else the forward crossing; else `None`, which now means only *never*. `recurrence_end_date`
is then DELETED and `is_retired` stops being a BOUND discriminator (it stays for badging). It
deletes a defect rather than ruling on one: a retired loan's stop tracked `ctx.as_of`, so the
admitted set GREW one occurrence per cadence period where the stored column froze, and at `R7d-c-2`
that writes a past-dated row per pass against a debt that is gone. A loan closed then trued back UP
has two crossings and the rule takes the LATER, **RULED `R-R51`** (developer, 2026-09-03): taking
the first stops recurrence across a span the loan genuinely owed, which under-generates.

**Scope.** A NEW producer beside `loan_payoff_date`, not a change to its contract, so the card,
`/savings` and the equity chart move one at a time; deleting `loan_payoff_date` at its last consumer
is a LATER `balance` step, not minted here. No migration.

**It DOES move a stored value, and the first draft of this line said it did not.** An adversarial
review caught it and the state was then reproduced: `sync_recurring_payment_bounds` WRITES the
derived answer into `recurrence_rules.end_date`, so every retired loan's stored bound changes at the
next chokepoint -- usually one past date for another, but for a loan cleared BEFORE its first
installment the stored pair becomes `[2026-07-15, 2026-06-21]` and is inverted PERMANENTLY, where
the old `ctx.as_of` bound drifted past `starts_on` and healed. The refusal `refuse_inverted_window`
reads that stored pair whenever a submission omits both bound keys, and the form locks a loan
payment's start and "Ends" controls but NOT its "Repeats" select -- so the owner was refused on an
edit still offered to them, with no control that could fix it. The remedy taken is the one the CHECK
ruling already implies: that door SKIPS a definition whose window the app derives
(`owns_validity_window`), because a derived empty window is a correct answer and refusing on it
makes the door the constraint `ck_recurrence_rules_valid_window` declined to be. `R7d-f` moves the
rest of that module's loan-payment reads off the column.

- [ ] **R7d-d -- the DISPLAY readers take the resolver, through a COMPOSED DOOR.**

`recurring_view` and `describe` stop reading the column, so the Recurring surface's cadence sentence
and its next date name the derived payoff rather than the last value a chokepoint wrote.

**It reaches as far as the COMPOSED VALUE** (developer, 2026-09-02, over two smaller options): the
conjunction of the authored bound and the derived stop is a VALUE the walk already reads
(`ResolvedRecurrence.closing`), not a narrowing each of five surfaces performs, and the answer
SHAPES move to `recurrence/_closing.py` because `_describe` cannot import `loan_recurrence_sync`
without a cycle. **BLOCKED on `R7d-h`**, whose absence this step first papered over with a fourth
shape. Held at `ea61116d` (`feat/r7d-d`, no PR) -- composed value, door and type move built and
green; the display readers are NOT moved and the step may not tick until they are.

- [ ] **R7d-e -- `/obligations` and the emergency-fund baseline take the resolver.**

`obligations_aggregator.has_ended` asks the resolver for a loan payment, so a RETIRED loan's payment
leaves the committed-monthly total and `savings_goal_service`'s baseline on the day the loan is
finished, not the day a chokepoint last ran. **MOVES MONEY** on both.

- [ ] **R7d-f -- the FORM's "Ends" control, its refusals and its preview.**

`owns_validity_window` SPLITS per bound (see above), the locked control renders the resolved answer,
the inverted-window refusal stops reading the column, and R-R30 decides what a baseline-less owner
sees. **`update_recurrence_rule_from_form` READS THE BOUND AND WRITES IT BACK** on every unrelated
edit of the template (`_recurrence_form_helpers.py:617` into `reauthor_rule`) -- invisible to any
NULL-the-column census, because the difference is in what the next save persists. Owes
`tests/manual/verify_recurrence_form.py` a browser pass.

- [ ] **R7d-g -- the column stops being WRITTEN, and the CHECK lands.**

Nine of the ten call sites go, `end_date` goes NULL for every loan payment in a migration, and
`ck_recurrence_rules_valid_window` is added -- true by construction, because the only rows that
could invert it no longer store a closing bound. Decide first what repairs the three shapes
`_sync_loan_cadence` covers -- and note it does the SAME read-and-write-back round trip, inside the
module this leaf rewrites, so it OUTLIVES the deletion unless named: finding **D50**, re-pointed
here from `R7d-c` on 2026-08-27. **D35 only HALF closes** (`starts_on` stays derived AND persisted
under R-R29), so re-point that row rather than ticking it.

- [ ] **R7e -- the recurrence form's three-state fields become ONE typed submission.**

A recurrence field has three meanings -- *stated*, *cleared*, *not mentioned* -- and HTML form data
has two. The schema's `@post_load` emits a `RecurrenceSubmission` whose fields are each either an
`UNSET` sentinel or a value, and `update_recurrence_rule_from_form` applies it uniformly
(`replace(current, **submission.applied_to(current))`) instead of reading key PRESENCE field by
field.

**Today the third state is a coincidence of the schema declaration.** `_normalize_empty_inputs`
keeps an empty string as a present `None` for an `allow_none` field and drops the key for the rest,
so "cleared" and "not stated" are told apart by whether a field happens to be nullable -- and every
field that needs the distinction grows its own read at the route: `states_a_start = KEY in data` for
`starts_on`, `ctx.end_bound is not None` for the closing bound, and for `due_day_of_month` the read
was simply MISSING. R8's business-day shift is the fourth.

**Both halves of that gap were live at R7c-b.** An amount-only PATCH silently erased a stored
`due_day_of_month`; and the Due Day row is hidden for a cadence anchoring on a paycheck but was
never DISABLED, so a value typed under "every 1 month" still posted after the switch --
`posted=['25']`, caught by `tests/manual/verify_recurrence_form.py` and invisible to the whole
pytest suite. R7c-b made the three fields AGREE (absent keeps, empty clears) and left the reads in
place; this step removes them. Closes **D36**.

- [ ] **R7f -- what a PROGRAMMATICALLY created recurrence starts on.**

`investment.create_contribution_transfer` and `salary/profiles.py`'s profile-template builder both
seat their rule at `calendar.opening_bound()`, so a new contribution or salary profile fans rows
across every pay period the owner has, closed ones included. **MOVES MONEY**, so it is ruled before
it is built.

That is what both routes have always done -- an absent opening bound resolved to the same value
before R7c-b made `starts_on` required -- and R7c-b STATED it rather than changing it, because
changing it was outside that step. The FORM took the other answer for a measured reason:
`create_form_default_starts_on` defaults to today, and the empty state it replaced wrote 5 backdated
rows worth `$10,000.00` into pay periods that had already closed.

The same two lines pass a `date | None` into a `date` field, whose only disposition is
`RecurrenceSpec`'s "states no starts_on" refusal -- a message written for a form, reached by a route
that has none. Whatever this step rules, it states the value honestly at both sites. Closes **D34**.

- [ ] **R8 -- the DECOMPOSED parent of the ruled add-ons.** Split 2026-08-16 (**R-R23**) on a
      measurement, not on size: three of the four add-ons cannot deliver what they promise before
      **R5**. `recurrence_engine.compute_due_date(rule, period)` never receives the occurrence
      (ledger row **D26**), so a generated row is dated from the rule's day of the month or from its
      PAY PERIOD and from nothing else -- and a weekly row, an nth-weekday row and a shifted row
      each name a date neither source can carry. The count-bounded end left at **R7b-3**.

- [x] **R8-a** `87e2c5b9` -- as built: `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R8-b -- the WEEK unit.** Blocked by **R5**, and the blocker is measured rather than
      inherited: with the router's refusal lifted a `(2, WEEK)` rule already resolves, walks, places
      and words itself correctly -- and every row it generates carries the funding PAYDAY, because
      `scheduling_day_of_month` answers `None` for a unit with no day of the month and
      `compute_due_date` reads `None` as "date this from the period". The step is therefore the
      DELETION of `has_row_date_coordinate` and its raising twin, which R5's `occurs_on` makes
      unnecessary rather than merely satisfied. A second measurement bounds the value: at the
      developer's 14-day cadence `(1, WEEK)` puts TWO occurrences in one paycheck, which
      `idx_transactions_template_period_scenario` cannot hold and
      `_recurrence_common.refuse_unstorable_repeats` refuses -- so weekly-by-date needs R5's re-key
      as well, and only `(2, WEEK)` and coarser are storable before it.

- [ ] **R8-c -- the nth-weekday coordinate.** Blocked by **R5** for the same reason: a "third
      Tuesday" rule has unit MONTH, so `scheduling_day_of_month` answers `starts_on.day` -- the
      anchor's incidental day -- and every generated row is dated on the 17th of its month rather
      than on that month's third Tuesday. That is ledger row **D29**'s display defect in the DATE.
      **RULED 2026-08-16 (R-R25)**: the two fields go on `budget.recurrence_rules` as an EXCLUSIVE
      ARC under one CHECK, and `budget.recurrence_weekday_anchors` is DROPPED unwritten. The plan
      said this invariant becomes "a CHECK against `recurrence_rules.nominal_day`", which is not
      buildable -- a PostgreSQL CHECK cannot reference another table -- and the column form is what
      ruling **R-R16** already did for `nominal_day` and R7c-c already did to the unwritten
      `recurrence_month_anchors`. `_describe._coordinate` must then dispatch on the coordinate KIND
      rather than on "WEEK or else".

- [ ] **R8-d -- the business-day shift.** Blocked by **R5**, and this one cannot even be OBSERVED
      before it: the shift moves an OCCURRENCE and the write loop discards it (**D26**), so no
      stored row's date would move at all -- only which paycheck the occurrence places into.
      **RULED 2026-08-16 (R-R26)**: "non-business day" is weekends plus the eleven US federal
      holidays DERIVED as rules rather than seeded as rows, which needs no per-year migration and
      composes with the nth-weekday machinery R8-c builds. The shift applies to the CASH date
      only -- a bill due Aug 1 paid Friday because Aug 1 is a Sunday still satisfies the Aug 1
      installment, so `due_on` is never shifted. `RecurrenceSpec` carries no `shift` field today and
      `resolve` hardcodes `NONE`; 46 of 46 live rules carry `none`. Finding **F-4** (the PAY
      SCHEDULE's own holiday shift) is a different question and stays separate.

### R10 -- the regeneration's own defect

Found while X-f3b measured the ledger. Its two leaves are below.

- [x] **R10-a** `5fc13cdb` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R10-b** `ea776528` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R11 -- the LEAD placement: fund an occurrence from an EARLIER paycheck.**

**Opened at plan step R8-a, out of what closing ledger row D20 left behind.** D20 said the placement
axis has no "the LAST paycheck on or before the occurrence", so a bill funded IN ADVANCE is
inexpressible, and both halves were measured false: `CONTAINING_DATE` funds from the paycheck whose
span COVERS the occurrence, so its payday is on or before that date by construction -- 0 of 305
seated occurrences across five pay cadences funded after theirs -- and the remedy it named is not a
third rule, because `PayCalendar.period_starting_on_or_before` disagreed with `period_containing` on
0 of 8,460 days of a tiling calendar and past the horizon answers the LAST saved paycheck, which
would seat every future occurrence of every rule in one.

What is genuinely inexpressible is a LEAD: funding from a paycheck EARLIER than the one containing
the occurrence, so rent due 1 August is paid from the 17 July paycheck rather than the 31 July one.
**MOVES MONEY**, so it opens with a ruling and not a keystroke, and the ruling is what the lead is
MEASURED IN: one paycheck back (`PayCalendar.period_starting_before` over the containing period's
own payday, which already exists and needs no new search), or a lead in DAYS placed at
`occurrence - lead_days`, or a lead in days that then places by the CONTAINING rule. The first adds
no authored value and cannot express "three days early"; the last two add an integer to
`budget.recurrence_rules` and need their own domain.

Whatever wins, three things follow that R8-a's code already names: `_describe._placement_note` must
word the new member or RAISE (it is total over the enum), `_parenthetical`'s day-1 collapse is
guarded on the deferring placement precisely so a LEAD cannot silently inherit it, and
`fires_on_day_of_month` stays `False` for it -- so its rows are dated from the funding payday, the
same deliberate state the deferring placement carries under **D26**. Closes **D40**.

- [ ] **R12 -- the deploy script's refusals get a test.**
**Opened at plan step R9, by two independent adversarial reviewers of it.** R-F8 built
`deploy/shekel-deploy.sh`'s two predicates -- `preflight_migrations`, which refuses a TARGET image
that cannot resolve the database's stamp, and `repin_is_safe`, which after a failure decides whether
re-pinning the previous image recovers or kills. Ruling **R-R27** rests R9's one-release drop of
`ref.recurrence_patterns` on the second of those, and R9 deleted `TestDeliberateRefSeedSurplus`,
which was the last EXECUTABLE statement of the hazard that refusal now covers. An executable guard
was replaced by an unexercised one.

**Nothing in the repository drives the script.** `.pre-commit-config.yaml` scopes to `^app/`,
`^scripts/` and `^tools/`; CI lints `app/` and `scripts/`; `pytest` collects `tests/` and
`tools/plan_gate`. `shellcheck` reads it but says nothing about behaviour.

The step DECIDES first where a shell harness runs -- a `tests/` module shelling out to `bash`, a
`bats`-style suite with its own runner, or a Python port of the two predicates with the shell
calling it -- and then covers at minimum: a stamp the target cannot resolve is refused; a stamp the
OLD image cannot resolve refuses the re-pin; an unchanged stamp after a container that never started
still re-pins; and an unreadable `alembic_version` is treated as unsafe. Each arm shown FIRING,
which is the standard the arc's own verification file sets. Closes **D41**.

**The semi-monthly case is ruling `R-R28`**, which lives in `rulings.md` like every other and is
cited by step **R13** below. It was a PARAGRAPH here until `balance:X-ao-2a` -- outside this
document's own rulings table, so the lift that read the tables would have left it behind.

**`R-D33` and `R9` left this index on 2026-08-19** with their `steps.md` rows, archived as one
completed span to `historical/recurrence_completed_findings_span_as_built_2026-08-19.md` (rule 5)
when `R-F16` needed the room. Each closed a finding on its own commit and blocked nothing; that
record names both hashes and says why `R7c-c`, `R7c`, `R7a-2a` and `R-F1` stayed.

### Carried steps -- scheduled here so they are not merely remembered

Section 5's ledger carries findings that no numbered step closes: some this arc surfaced elsewhere
and does not own, and some it left in its OWN code and chose not to fix inside a commit that
promised something else. They get steps here so they are scheduled rather than remembered.
**None blocks R1-R9, and none is blocked by them**; each is a standalone commit that can run in any
gap. Do not fold them into a recurrence migration -- an unrelated fix riding in a schema migration
is unreviewable.

**R-F2, R-F3 and R-F8 left this list on 2026-08-17** with their `steps.md` rows, archived as one
completed span to `historical/recurrence_findings_span_as_built_2026-08-17.md` (rule 5); that record
names all three hashes and says why `R-F1` stayed.
**`R-F1`, `R7a-1`, `R7a-2a` and then `R-F16`, `R-F17` left the same way on 2026-08-27**, to
`historical/recurrence_completed_span_as_built_2026-08-27.md`. `R-F10` and `R-F12` could not: each
is identity-paired with a row in another arc (rule 11), so their entries stay here.

- [x] **R-F10** `fe365de1` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R-F12** `4f134bf4` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R13 -- a DAY-OF-MONTH pay schedule** (ruling **R-R28**).

`budget.pay_schedule` holds one fact, `cadence_days`, and every payday is a fixed-length walk from
the anchor. Semi-monthly pay is not: it is the 1st and the 15th (or the 15th and the last day), and
`round(365.2425 / 15) = 24` gives an owner the right COUNT with paydays that drift through the
month -- Jan 1, Jan 16, Jan 31, Feb 15. **Monthly already carries the identical limitation** (a
30-day walk is not "the 1st"), and pay-calendar finding **F-4** records that `pay_periods` stores
NOMINAL paydays generally, so this is one shape rather than a semi-monthly special case.

The step gives the schedule a cadence KIND -- fixed-days, or one/two days of the month -- and
branches THREE producers on it: `pay_period_write.record_paydays` (which spaces a batch),
`pay_calendar._derive.derive_periods` (whose last period's end is cadence-projected), and
`PayCadence.periods_per_year` (which must answer 24 without dividing). It is ranked last in this arc
deliberately: it edits the pay-calendar package's core, which `pay_calendar:C2`'s remaining leaves
are still moving, and nothing in either arc depends on it.

- [ ] **R14 -- what a payroll deduction's gross is priced from** (finding **D45**).

`investment_projection._compute_deduction_per_period` divides a profile's stored `annual_salary` by
the paycheck count, where every sibling surface routes through
`income_service.get_current_gross_biweekly`. It is the recompute F-20 / MED-06 / F-032 replaced
everywhere else, and it sets both the employee contribution and the employer-match basis: measured
at `$137.51` a year understated on the developer's own 5% employer contribution.

**R-F16 fixed this and reverted it, and the revert is the specification.** Swapping in the
owner-level raise-aware gross made every percentage deduction price off ONE profile, chosen by
`get_current_gross_biweekly`'s unordered `.first()` -- a measured 39% swing on a two-job owner that
flips between renders with no data change -- and off a figure that is ZERO whenever no period covers
today, which deleted the whole contribution plan at onboarding and after a horizon lapse. So the
step must answer three questions together rather than one: WHOSE salary (the deduction's own
profile, which the per-row basis gets right), WHICH period's gross (the engine already computes one
per period, and a contribution TIMELINE wants that rather than one current-period scalar), and
against WHICH clock -- ledger row **P56**'s question, since a scalar resolved at `date.today()`
makes a historical modelled balance move when a raise lands.

**MOVES MONEY** and needs its own review pass.

- [ ] **R15 -- what a payroll deduction's own FREQUENCY means** (finding **F-21**).

`salary.paycheck_deductions.deductions_per_year` server-defaults to `26` and the salary form offers
exactly three values -- `26 (every paycheck)`, `24 (skip 3rd paycheck)`, `12 (monthly)`. It is never
multiplied or divided: `paycheck_calculator._deduction_applies_at` compares it against `24` and `12`
and nothing else, so it is a three-valued MODE wearing a biweekly paycheck count, compared in Python
and again in `_deductions_section.html` -- which is the "IDs for logic, strings for display" rule of
`CLAUDE.md` with an integer in the string's place. **A THIRD reader ignores it altogether**
(**N-395**): the investment contribution timeline pays every deduction on every payday, so this step
APPLIES the frequency as well as ruling it. At a weekly cadence "every paycheck" is 52 and "the 3rd
paycheck of the month" names nothing; at a monthly one only the third option survives.
**11 of the developer's 12 live deductions carry `24`**, the mode that generalises least, so the
migration has real rows to re-express.

**Its own ruling first**: whether the mode becomes a `ref` table, whether "skip the 3rd paycheck"
generalises to "skip the Nth" or is retired, and what the three live values become. No figure
moves -- nothing computes from the number -- so it is a migration and a form change rather than a
money step. Sequenced behind **R14**, which re-opens the same table for what a deduction is PRICED
from.

- [ ] **R18 -- a paycheck's EARNINGS side gets LINES, as its deductions side already has.**

`paycheck_calculator.Earnings` is four scalars -- `annual_salary`, `gross_biweekly`,
`taxable_income`, `net_pay` -- and `gross_biweekly` is
`gross_per_paycheck(annual_salary, periods_per_year)`, a pure function of the salary and the
cadence. `net_pay` then only ever SUBTRACTS. So there is no way to add a dollar to a paycheck that
is not an annual-salary raise. `DeductionBreakdown` beside it holds LISTS of `DeductionLine`, each
filtered by `_deduction_applies_at` on a `deductions_per_year` of 26 / 24 / 12 against
`_is_third_paycheck`: **deductions have a cadence and earnings have none.** `PaycheckDeduction` also
carries `ck_paycheck_deductions_positive_amount`, so a negative deduction -- the natural spelling of
an employer-paid allowance -- is unrepresentable at the database, and
`salary_profiles.additional_income` is W-4 Step 4(a) and feeds only withholding.

**Measured on the developer's own data 2026-09-01, which is why this is a step and not a
preference.** His Health Insurance Allowance is paid on 24 of 26 paychecks and his Phone Allowance
on the first payday of each month -- *exactly* the two cadences `_deduction_applies_at` already
implements, on the wrong side of the paycheck. Having nowhere to put them he modelled both as
separate income templates, and it has cost twice: template 2 is a `period` / every-period rule with
an end date, so a 24-of-26 benefit modelled as 26-of-26
**would have generated a `$100.00` income row on 2026-07-30 the employer does not pay** -- the first
three-payday month in the data, and it failed to fire only because the benefit ended 2026-06-30
first -- and template 3's `month` / `period_starting_on_or_after` rule put rows in the wrong pay
period **2 times out of 6**, both cancelled by hand.

**What it would delete.** One deposit becomes one app row, so the exact tier explains the
developer's payroll deposits with no group, no residue and no attribution control -- which is the
entire population `bank_import:X-gj-3a` was built for and the whole of what `bank_import:X-gj-3b`'s
group rule would have been for -- and on 2026-09-02 the developer acted on that rather than filing
it: **`X-gj-3b` is WITHDRAWN** (ruling `bank_import:R-JJ`) and this chain is re-ranked up in its
place. It also opened **D59**: the model cannot express a 24-of-26 cadence at all, so the
developer's own 24-of-26 benefit would have minted a `$100.00` income row on 2026-07-30 and failed
to only because its `end_date` got there first. It does NOT delete `DifferenceLanding`: a genuine
multi-row deposit (a paycheck plus a reimbursement, two jobs on one deposit) still needs it.
**`X-gj-3a` is therefore a correct interim whose population would go to ZERO if this shipped**,
which is the honest sequencing statement and is why this row exists rather than living in a commit
message.

**Its own ruling first**, and it shares R15's subject: whether an earnings line reuses
`deductions_per_year`'s mode or the two are unified, whether an allowance is taxable, and what
happens to the two live income templates and every row they have generated. Sequenced behind
**R15**, which rules what that mode MEANS -- building earnings lines on a mode R15 may retire would
be designing over a shape the next step deletes. **MOVES MONEY** (it changes `net_pay`), needs a
migration, and needs its own review pass.

- [ ] **R16 -- the DECOMPOSED parent of the ESTIMATED tier's summing.** Split into FOUR leaves
      2026-08-26 (**R-R36**) when a trace found the forward fold charging one month of interest per
      payment RECORD: while the accrual rode on the payment, no cadence could be honoured and no
      second definition summed. Carries **D47**, **D48**.

- [x] **R16-a** `e8baa3c0` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R16-b -- the DECOMPOSED parent of the summing.** Split into TWO leaves 2026-08-27
      (**R-R37**); the sum waits on `end_date` holding one fact. Carries **D47**, **D48**, **D53**.

- [x] **R16-b-1** `1b818135` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R16-b-2 -- the ESTIMATED tier SUMS every definition, on each one's OWN cadence** (findings
      **D47**, **D48**, **D53**).

**One tier of three asks a question the other two do not.** The SETTLED fold
(`loan_loaders.query_shadow_income`) and the PLANNED tier (via `projected_income_shadows`) take
every transfer into the loan ACCOUNT with no template filter, and `split_payment_cash` routes
anything above interest + escrow to principal. Only `standing_installment_cash` prices from ONE,
picked by `active_recurring_transfer_template`'s `.order_by(id).first()`.
**Re-measured 2026-08-27 on `feat/r16-b`, fresh clone, `as_of` 2026-08-27**: re-pointing the real
`$500.00` every-paycheck sweep at the Mortgage (id 1, no forcing) prices every uncovered installment
at `$500.00` against a `$616.99` escrow -- the payoff reads `None` and the balance GROWS to
`$1,059,869.99` by 2053-12-01, where summing each definition's occurrences on the contract's charge
calendar answers `2034-10-01`, which a closed-form amortization of the same stream independently
lands in the same month as. **The `2036-04-01` recorded 2026-08-26 does not reproduce** under that
prototype.

**After R16-a the cadence is not a special case** -- each definition emits its occurrences into the
payment stream -- **but the CHARGE CALENDAR must move with it, and that is this step's real work**
(its own adversarial review, 2026-08-26, refuted the sentence that said the charges do not move).
`_charges_for` derives one charge per month the PAYMENTS occupy, which is exact only while the
ESTIMATED tier fills every month; this step deletes that fill, so a definition SPARSER than monthly
collapses the charge set with the payment set -- a quarterly loan payment, authorable today through
`POST /transfers`, folds 40 charges where the contract owes 120 and reports `$367.98` of interest
against `$1,096.34`. The charge calendar therefore comes off the payments and onto the loan's own
contractual installment sequence here, and **D53 is ruled here rather than at R16-c** for the same
reason. The `PERIOD` unit needed a walk that reaches past the saved horizon, which is what
**R16-b-1 shipped** -- until it, `occurrences()` truncated there in silence and a naive walk to
payoff under-generated by seven years. A second escrow-INCLUSIVE definition also has to be told from
an extra-principal payment, since only the first clears the period's escrow. It takes the tie-break
off the PRICING path -- `standing_payment` and `StandingPayment`'s singular shape -- and does
**not** delete the query: three loan-side readers legitimately need ONE row (the dashboard's
extra-principal prefill and the two routes that MUTATE a settings row), and the investment
dashboard's "has a recurring contribution" is a different question again. Which row those three
write is **D49**.

- [ ] **R16-c -- the PAST and the FUTURE become ONE event STREAM**

`loan_ledger._walk._replay_events` and `balance_at._plan_fold._split_plan` were two running-balance
implementations of one rule. **That half is done**: `balance:X-au-g-2c-3b-2` (`3b7716f8`) moved the
rule to `loan_ledger._replay.replay_loan_events` and both tiers now call it, which also made the
settled walk charge once per accrual period and CLOSED **D51**.
**This step SHRANK because part of it SHIPPED EARLY, not because scope was dropped.** What is still
owed is the STREAM, not the arithmetic: one event list with `as_of` marking where recorded fact
becomes projection, one seed, and one set of record types, so the two tiers differ in nothing but
their inputs.

**D51 is CLOSED, and it closed OUTSIDE this arc**: at `balance:X-au-g-2c-3b-2` (`3b7716f8`), which
made the settled walk charge once per accrual period. It is recorded here rather than only there
because a reader of THIS arc must be able to find where a finding this arc owned actually went;
`steps.md`'s row for that balance step carries the forward half ("satisfies **recurrence:D51**"),
and this is the backward half.

*Its predicate still greps TRUE and the defect is gone*, which is the trap worth recording: a row
whose WORDS still match while its defect is gone, and a row whose defect remains while its words
stop matching, are the SAME failure, and only re-reading the code separates them.
`_walk._replay_events` does still call `split_one_payment` once per settled shadow -- but
**that call no longer CHARGES anything, and it no longer computes anything**: it copies the four
parts verbatim off the replay's outcome and names them. The charge is derived once per accrual
period from the installments the payments satisfy (`charges_for_due_dates`), the replay accumulates
it, and `apply_payment_cash` clears it at the FIRST payment in that period;
**a second payment in the same `installment_slot` finds nothing standing and pays pure principal.**
Measured on a forced due-month collision: interest `$1,014.06` -> `$0.00`, escrow `$616.99` ->
`$0.00`, principal `$279.90` -> `$1,910.95`.
**D53 is answered at `R16-b-2` and this step inherits the answer** -- the CONTRACT charges every
forward period (**R-R37**), repealing "an overdue slot with no record ... holds flat" (B-9) for the
FUTURE half, which ruled what an unpaid installment PAYS and never what an unpaid month CHARGES.
What this step owes is the same rule for the PAST. **MOVES POSTED MONEY, OWN PR.** It also owes
**D54** (a month the SEED already charged is charged again by the plan) and **D55** (the accrual
period is the CALENDAR month where the contract's is the installment month -- `$1,629.94` on which
side of a boundary an extra payment falls).

- [ ] **R16-d -- the accrual CONVENTION becomes a value on the loan** (finding **D52**).

`accrue_monthly_interest` hardcodes `balance x rate / 12`: a US fixed-rate mortgage's convention,
not a simple-interest auto loan's daily actual/365 -- so the app tells an auto-loan owner that
paying early saves nothing (`$8.88` over the Van's remaining life). A
`ref.interest_accrual_conventions` row, a column on `budget.loan_params`, and one total
`accrued_interest(balance, rate, from, to, convention)` of which today's formula is one member.
**Every live loan backfills to `MONTHLY_1_12`, so the migration moves `$0.00`** and changing a
loan's convention becomes a deliberate act with evidence rather than a side effect. It retires
`debt_strategy_service._accrue_interest`, which inline-copies the formula under a docstring claiming
it matches the engine. Which convention each loan's NOTE states is an `operator` question the
backfill does not guess.
