# Implementation Plan: Recurrence Rule Redesign

## Where this stands

**Plan of record** for the two-axis recurrence model and the cash-date / installment-date split.
R1-R4 and the R7c cutover are ARCHIVED; the closed pattern set is GONE, which is what this arc was
for (R-R16 / R-R18 / R-R27). Which steps are in PRODUCTION is a measurement, never a stored value:
`git branch -r --contains <hash>` against `origin/main`.

**R7d SHIPPED WHOLE 2026-09-14** (R-R33 / R-R34 / R-R38 / R-R80..R-R83 / **R-R88**): every reader is
on the resolver and the WRITE is gone. **A tie-break is a sign the SEARCH is the wrong question**
(R-R35): only ONE tier of three asks "which transfer into a loan is its payment", and **R16**
deletes the rest (four leaves, **R-R36**): `R16-b-2` (`7e2e6413`), `R20` (`b4da8068`, **R-R72** part
3) and `R16-c-1` (`c88ed6ba`, **R-R90**: ONE event stream) shipped, and `R23` (`f3bf8b9d`,
**R-R98**) moved a balance a migration had dated its own run day to the setup day. `R16-c-2` (the
contract calendar, **R-R89**; MOVES POSTED MONEY) is next. **JUST LANDED: `R5-a` (`b0e1322a`)**: a
row is dated from its occurrence, the rule's due day gone (**R-R94**..**R-R97**, **D18**).

**What to do next is `steps.md`'s order table; do not re-derive it here.** Section 4 is the steps;
the findings (`ledger.md`), the index, the rules and `verification.md` are the shared registries in
`docs/plans/`.

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

**RULED 2026-09-03 (R-R52)**: `R5` re-points behind `balance:X-bi-4`, not `X-f4` -- the X-f4 gate
rested on one shared file where R5's touch is a docstring line, merge hygiene and not a
dependency -- and `R6`, `R8-b`, `R8-c`, `R8-d` and `balance:X-k` follow R5 and inherit the move. R6
stays behind R5 (it reads `due_on`); the split-off accessor option was not taken.

**Consequence for Half A:** it must leave the `due_date` contract byte-identical so the R1 oracle
stays green, so no step before R5 touches the column. The transaction-template form's "Due Day of
Month" field went at `R5-a` (**R-R96**): a recurring due day that differs from the payment day is a
CONTRACT term, and it lives on the loan.

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
-- NEVER CREATED (R-R12): budget.recurrence_due_dates.  A loan's recurring
-- due day is a CONTRACT term on the loan (``loan_params.payment_day``,
-- R-R96), not a fact about the rule.

budget.transactions / budget.transfers          [R5-b, rulings R-R12, R-R94]
  occurs_on        DATE  NOT NULL   -- the date the CADENCE names
  pay_period_id    FK                -- the funding.  Already exists.
  due_on           DATE  NULL        -- a date someone STATED that the
                                     -- occurrence does not give (R-R94).
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
| `due_day_of_month` + implicit next-month rule | dropped at `R5-a` (R-R96): a recurring due day that differs from the payment day is the loan's contract term, `loan_params.payment_day` |
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

**The semi-monthly case is ruling `R-R28`**, which lives in `rulings.md` like every other and is
cited by step **R13** below. It was a PARAGRAPH here until `balance:X-ao-2a` -- outside this
document's own rulings table, so the lift that read the tables would have left it behind.

**`R-D33` and `R9` left this index on 2026-08-19** with their `steps.md` rows, archived as one
completed span to `historical/recurrence_completed_findings_span_as_built_2026-08-19.md` (rule 5)
when `R-F16` needed the room. Each closed a finding on its own commit and blocked nothing; that
record names both hashes and says why `R7c-c`, `R7c`, `R7a-2a` and `R-F1` stayed.

- [x] **R17** `4e8b40b3` -- as built: `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R5 -- a row's due date is DERIVED, and only a STATED date is stored**, the DECOMPOSED parent
      of two leaves (**R-R94**, **R-R95**, **R-R96**, 2026-09-23), cut by the coordinator: the date
      read from the OCCURRENCE with the rule's due day dropped (a), and the stored column's deletion
      (b), which waits for `balance:X-bi-6-4`'s transfer work. **R-R94** amends **R-R12**: the third
      home is a date someone STATED, and a loan's installment is its contract term (**R-R96**, step
      `R6`). Ticks with its last leaf.

- [x] **R5-a -- a row is dated from its occurrence.** `b0e1322a` --
      `compute_due_date(rule, occurrence, period)` through one body, `_row_day.date_row`: the
      occurrence, or the funding payday for a rule naming no day (**R-R94**, **R-R95**); a
      carried-forward override row takes its rule's first occurrence in the paycheck, else the
      payday (**R-R97**); migration `1c569c51b449` drops the rule's `due_day_of_month` and its CHECK
      (**R-R96**). Closed **D18**; filed **REC-537**.

- [ ] **R5-b -- the stored date goes** (**R-R94**'s column half). It waits for `balance:X-bi-6-4`,
      which re-parents a transfer's record half and may delete the `transfer_service` shadow
      `due_date` mirror (`_create` / `_update` / `_restore`) and `TransferLeg.due_date` outright, so
      this specification is RE-DERIVED on the tree that step leaves before anything is built.

Add `due_on DATE NULL` to both row tables, backfilled exactly where the accessor would not reproduce
the stored date (on the 2026-09-23 production clone: 10 live transactions, 3 live transfers and 51
deleted transfers, a shadow reading its parent; RE-MEASURE, since `balance:X-bi-6-4` and time move
it), and drop `due_date`; every live row keeps its date. ONE accessor -- the stated `due_on`, else
`R5-a`'s formula over the occurrence -- is read by every price, screen and loan reader and the
`TransferLeg` proxy; the owner doors write `due_on`, and a one-off keeps `occurs_on` through
`one_off.state_due_date`. `DerivedRowFields.due_date`, `DerivedTransferFields.due_date`, the
maintain rewrite and `compute_due_date` itself are DELETED, and `build_transient_rule` is
re-examined: its last callers are tests needing a rule only because `compute_due_date` takes one
(carried from `R-F6`'s entry, archived 2026-08-19). Both `ck_*_template_row_needs_due_date` CHECKs
become `template_id IS NULL OR occurs_on IS NOT NULL OR due_on IS NOT NULL` until `R19-b` binds
`occurs_on` NOT NULL, when they can go (**R-R94**'s option said "deleted"; this is its precise
form), and `idx_transactions_due_date` is re-examined. The downgrade can restore `due_date` from the
accessor, since it is derivable, so whether it refuses is decided and stated.
**`due_date` is a POSTING INPUT**: each `_POSTING_RELEVANT_FIELDS` names `due_on`,
`loan_posting_service.backfill_all_loan_postings()` runs after the migration (the caveat
`c4e91a7b2d38` carries), and the posted ledger is proven identical. The Python files naming
`due_date` in code (census 64 code files `due_date` in `app/**/*.py`) are a SUPERSET of the column's
sites, and two templates carry `<input name="due_date">`, so the wire moves too. **Rule 14 hazard:**
`spending_analysis.py` spells the date in SQL (`COALESCE(due_date, PayPeriod.start_date)`), a second
producer of the accessor; the step names ONE walk. **Carried relays:**
`definition_unarchive._own_day_inside` (**R-PC96**) reads a hidden row's STORED `due_date` for
rule-less and `occurs_on`-NULL rows and must still get the day the money lands; an **R-R97**
leftover dated from an occurrence it does not answer carries a STATED `due_on`, a no-occurrence one
the derivable payday; and nothing here may re-create **REC-537**. `loan_loaders/_terms.py`'s
installment reader is `R6`'s (**R-R96**): this step only re-points its read of the stored date and
answers IDENTICALLY. Closes **D26**.

- [ ] **R6 -- `payment_day` is the loan's contract day; one installment accessor** (**R-R96**,
      2026-09-23, revising this entry's "delete `payment_day`").

A recurring due day that differs from the payment day is a CONTRACT term, so it lives on the loan:
the Van Loan's terms say the 1st while its payment rule stays on the 22nd, when the money moves.
`loan_installment_date(...)` becomes the single derivation, each payment covering the first contract
day on or after its payment day, so a late-clearing payment keeps its installment.
**There is no `recurrence_due_dates` table and there will not be.** The files carrying `payment_day`
in code (census 19 code files `payment_day` in `app/**/*.py`)
**already read it as the installment, bar one** -- `loan_recurrence_sync.py` makes it a CASH day
(`loan_cadence_start`), and that is D4's mechanism, which this step fixes;
`routes/loan/payment_transfer.py`, which once typed `day_of_month=payment_day` itself, now calls
that producer and names `payment_day` only in a comment (:190), so it is not among the code files.
The other files of the (census 30 files `payment_day` in `app/**/*.py`) that name it at all carry it
only in comments or string literals, which a code census blanks by construction, and not every one
of those is prose: `routes/loan/_helpers.py`'s `_PARAM_FIELDS` keys a form field by the string. The
Van's stored `payment_day` is corrected from 22 to 1 here too. Eight distinct producers of "when is
this installment due" collapse into one; the plan previously counted them as one accessor plus a
rule read. Kills D4. **This step needs its own review pass** -- it is the deepest cut into the
ledger.

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

- [x] **R7d** `b5ac8710` -- a loan payment's CLOSING bound stopped being a stored column: seven
      leaves (**R-R34**) with `R7d-g`'s three inside; ticked with `R7d-g-3`. The span as it stood:
      `historical/recurrence_r7d_as_built_2026-09-14.md`.

- [x] **R7d-a** `89cb0c1d` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R7d-b** `0462dc38` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R7d-c** `b8509c1e` -- generation takes the resolver; two leaves (**R-R38**). Archived with
      `R7d`.

- [x] **R7d-c-1** `61d81c7f` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R7d-c-2** `b8509c1e` -- generation resolves the stop through the composed door (**R-R56**,
      **R-R65**, **R-R73**). Closed **D46** via R16-b-2. Archived with `R7d`.

- [x] **R7d-h** `83dd4b8a` -- ONE closing date, past and future (**R-R51**); `recurrence_end_date`
      deleted. Archived with `R7d`.

- [x] **R7d-d** `4a839587` -- the DISPLAY readers took the resolver through `recurring_definition`
      (**R-R56**). Archived with `R7d`.

- [x] **R7d-e** `89302ba4` -- the monthly totals took the resolver (**R-R57**). Opened **N-513**,
      **N-514** (closed at R7d-f-2). Archived with `R7d`.

- [x] **R7d-f** `48e78700` -- the FORM's "Ends" control, five leaves (**R-R61**). Archived with
      `R7d`.

- [x] **R7d-f-1** `6af50d53` -- the locked *Ends* row reads the resolver. Closed **N-511**; opened
      **REC-515** (closed at R7d-f-2). Archived with `R7d`.

- [x] **R7d-f-2** `1d1f466f` -- the horizon rides on `RuleReading` (**N-514**, **N-513**,
      **REC-515**; **R-R75** closes balance:BAL-483). Archived with `R7d`.

- [x] **R7d-f-3** `e3661f6f` -- a stated stop is refused at create (**R-R60**, **R-R74**). Closed
      **N-512**. Archived with `R7d`.

- [x] **R7d-f-4** `e0c67c0b` -- the UPDATE door settles the destination first (**R-R76**,
      **R-R77**). Closed **REC-521**; opened **REC-522** (closed at R7d-g-2). Archived with `R7d`.

- [x] **R7d-f-5** `48e78700` -- `LoanDestinationLocks` per edit (**R-R78**, **R-R79**). Archived
      with `R7d`.

- [x] **R7d-g** `b5ac8710` -- the closing bound stopped being WRITTEN; three leaves (**R-R80**..
      **R-R83**); ticked with `R7d-g-3`. Archived with `R7d`.

- [x] **R7d-g-1** `a776b9df` -- nine of ten write sites went, the tenth became
      `sync_loan_payment_start`; migration `bf50951a3599`. Closed **D56**. Archived with `R7d`.

- [x] **R7d-g-2** `547cc43d` -- the opening bound's maintenance contract is complete at every door
      (**R-R84**..**R-R86**). Closed **D35**, **D50**, **REC-522**. Archived with `R7d`.

- [x] **R7d-g-3** `b5ac8710` -- the card per definition, the doors by template id (**R-R83**); the
      resolver's loan-level `extra_principal` DELETED (**R-R88**: a derive-mode row's cash already
      carries its definition's extra, so the composer paid it twice on every row-covered month,
      `$0.00` live); band chart, lever, allocation bar and schedule page re-cut onto the seam's
      forward plan, `monthly_override` gone. Closed **D49**, **REC-524**, **REC-525**, **REC-526**.

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
      composes with the nth-weekday machinery R8-c builds. **The holiday set and the weekend rule
      live in the ONE business-day module `pay_calendar:C14-a` builds** (**R-PC47**, 2026-09-03), so
      this step CONSUMES it for the cash date rather than building a second copy. The shift applies
      to the CASH date only -- a bill due Aug 1 paid Friday because Aug 1 is a Sunday still
      satisfies the Aug 1 installment, so `due_on` is never shifted. `RecurrenceSpec` carries no
      `shift` field today and `resolve` hardcodes `NONE`; 46 of 46 live rules carry `none`. The PAY
      SCHEDULE's own shift (once **F-4**, merged into `pay_calendar:N-398`) is `C14`'s question and
      stays separate.

### R10 -- the regeneration's own defect

Found while X-f3b measured the ledger. Its two leaves are below.

- [x] **R10-a** `5fc13cdb` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R10-b** `ea776528` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R19 -- an UNDATED row is retained, never deleted** (finding **REC-516**, ruling **R-R63**).
      The DECOMPOSED parent, split at the developer's 2026-09-08 ruling into the guard and the root
      fix. `classify_maintain_work` read *this row answers no occurrence* as
      *the rule dropped this occurrence* and routed a mutable, record-free undated row to
      `work.retire` -- a HARD DELETE at `_maintain` and, worse, at `transfer_recurrence`, where
      `delete_transfer(soft=False)` takes the parent AND BOTH SHADOWS.
      **The delete was invisible to every count**: the create arm answered the freed occurrence in
      the same pass, so the table was the same size afterwards
      (`deleted_count: 1, created_count: 1`, every conflict count zero). Reachable because the only
      thing that ever filled `occurs_on` on an existing row, `scripts/stamp_occurrences.py`, was
      retired at `balance:X-bz`. Measured 2026-09-08: production reachable at 0 (its 6 undated
      transactions are immutable and its 54 undated transfers are 3 immutable and 51 soft-deleted,
      any restore of which arms one), the dev database at **598**.

- [x] **R19-a** `2f6bab81` -- the guard: an undated row goes to `retained_ids`, so the pass leaves
      it exactly as found and TELLS the owner. Retained rather than skipped silently because the
      definition has moved past a row it cannot place: a template repriced `$100.00 -> $250.00`
      leaves such a row at `$100.00` for good while suppressing the correctly-priced row its
      paycheck would receive. Both controls were shown to FAIL against the pre-fix branch.

- [ ] **R19-b -- `occurs_on` becomes NOT NULL**, which is what makes `R19-a`'s branch unreachable
      rather than merely quiet. A template-linked non-override row always records the occurrence it
      answers; `carry_forward_service` achieved this by flagging its rows `is_override` until
      `balance:X-bi-7a` (`eecef63d`) stopped flipping a rule-less definition's row (**REC-523**),
      and the one-time transfer branch
      (`routes/transfers/_instances._materialize_one_time_transfer`) does not -- so it owes a rule
      for what occurrence a one-time transfer answers, its own date being the obvious candidate. It
      deletes THREE fences: the NULL arm of `rows_claiming`'s claim query, `R19-a`'s branch with the
      forward guard beside it, and `idx_transfers_template_scenario_undated`.
      **Its backfill belongs in the migration** -- a hand-run script is what left this reachable --
      and **R-R46** is the obstacle to state and answer: no migration here may import app code,
      because `build_test_template.py` replays the chain from zero. Expect it to DECOMPOSE.

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

- [x] **R12 -- the image pins its locale.** `cde86066` -- `LC_ALL=C.UTF-8` set in the Dockerfile's
      runtime stage, `scripts/test.sh` and `.env.example`, and `create_app`'s first statement
      refuses any other value under every configuration (**R-R92**, extending **R-R54**); a test
      holds the Dockerfile's line to `PINNED_LOCALE`. Closed **F-15**. `D41` was DISCHARGED before
      it, not closed here: the deploy script's test landed at `398c332c`.

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

*`R14`, `R15` and `R18` -- the earnings-lines chain -- moved to the `salary` arc on 2026-09-03
(**R-SAL1**, ruled with **R-SAL2** and **R-SAL3**); their specifications are
`implementation_plan_salary.md`, section 4, under their unchanged ids.*

- [x] **R-F10** `fe365de1` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R-F12** `4f134bf4` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R13** `d79986c7` -- a DAY-OF-MONTH pay schedule (rulings **R-R28**,
      **pay_calendar:R-PC68**): ONE commit with `pay_calendar:C17-d-2`, whose specification is this
      step's -- the `Monthly` and `SemiMonthly` kinds, migration `3ec5291ca4e2`. **N-399** stayed
      open and re-homed to salary as **SAL-556**. Its argument:
      `historical/recurrence_r13_as_built_2026-09-13.md`.

- [ ] **R16 -- the DECOMPOSED parent of the ESTIMATED tier's summing.** Split into FOUR leaves
      2026-08-26 (**R-R36**) when a trace found the forward fold charging one month of interest per
      payment RECORD: while the accrual rode on the payment, no cadence could be honoured and no
      second definition summed. **D47** and **D48** closed at `R16-b-2`.

- [x] **R16-a** `e8baa3c0` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R16-b -- the DECOMPOSED parent of the summing.** `7e2e6413` -- ticked with R16-b-2, its last
      leaf; split into TWO leaves 2026-08-27 (**R-R37**).

- [x] **R16-b-1** `1b818135` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R16-b-2 -- the ESTIMATED tier SUMS every definition on its own cadence.** `7e2e6413` -- as
      built: rulings **R-R64** to **R-R73**; harness `tests/manual/verify_loan_plan_sum.py`
      (baseline byte-identical over both loans; a planted `$500` sweep into the Mortgage `None` ->
      `2034-10-01`; the reset hole `2029-03-22` -> `2029-02-22`; August charged once, 91 -> 90).
      Closed **D46**, **D47**, **D48**, **D53**, **D54**; opened **REC-517** (R16-f), **REC-518**
      (R5), **REC-519** (closed at R20), **balance:BAL-483** (closed at R7d-f-2, **R-R75**).

- [x] **R20 -- the setup door records the stated balance as the assertion it is.** `b4da8068` -- as
      built, on `b141e779` (the loan anchor doors moved to `loan_anchor_service.py`): a
      `tracking_start` at the owner's "as of" day whenever the loan originated before it; migration
      `22b23085394d` dropped `LoanParams.current_principal` and its CHECK (0 of 2 on the 09-19
      clone; production prints its count); the earliest-payment refusal deleted (**R-R72** part 3).
      Closed **REC-519**. Spec and notes: `historical/recurrence_r20_as_built_2026-09-19.md`.

- [x] **R23 -- a balance a migration dated its own run day takes the setup day.** `f3bf8b9d` -- as
      built: migration `cddb15ffba5f` records the balance `d3d25212504b` had copied as a
      `tracking_start` on the setup day and withdraws the copy through the new append-only
      `budget.loan_anchor_withdrawals` (**R-R98**; the downgrade deletes only that statement,
      **R-R99**); the walk and the R-EQ door read ONE producer, `load_standing_loan_assertions`.
      Closed **balance:FU-1**.

- [ ] **R21 -- the walk's placement runs backward for a stated owner** (**R-R87**; finding
      **REC-527**, born at `salary:R15-b`'s review): `paychecks_from` and `_first_occurrence` read
      the calendar as `balance:X-bh-2` made it run below the record when `history_opens_on` is
      stated; generation still needs a saved period. `$0.00` today; graded by re-pricing a stated
      owner's backdated paydays against the ordinal rule R15-b retired. Revisits the 2026-09-11
      schedule bound (**R-R64**) for stated owners only. It also owns **REC-518** (the developer,
      2026-09-23, at R5's design fork): "the same boundary seen from generation: an occurrence
      before the schedule opens should place nowhere, or on a back-dated paycheck, never on the
      first saved one. Same code, one fix."

- [ ] **R22 -- the plan is computed, only the owner's acts are stored** (a DESIGN step: an audit,
      then forks to the developer with worked dollars, BEFORE any build. Asked for by the developer
      at `pay_calendar:C18-a` round 9, 2026-09-23: "The from scratch design needs to be a step in
      the plan"). **Today** every occurrence a recurring schedule names inside the saved paychecks
      is a STORED row (Projected until settled), so an unpaid copy can be stranded, revived or
      re-dated by any door that writes rows, and each such door has grown its own fence: the books
      refusals (**R-PC88**, **R-PC90**, **R-PC91**, **R-PC93**), the unarchive guard (**R-PC95**,
      refined by **R-PC96**), the revert stopgap (**R-PC97**), and three open rows -- **REC-534** (a
      day change deletes an unpaid row with only "updated"), **REC-535** (the conflict chooser
      revives a deleted row the schedule no longer names), **REC-536** (a hand delete and the
      archive share `is_deleted`). **The from-scratch model**: the schedule's occurrences are
      COMPUTED on every read (the balance seam already projects them past the saved paychecks for
      the loan estimates), and only what the owner DID is stored -- a payment (the record half), a
      skipped or cancelled occurrence (an exception on the rule, REC-536's own remedy), a price or
      paycheck change to one occurrence. A revert then deletes a record and the schedule says
      whether anything is still owed; an occurrence inside the books is owed nothing
      (**R-PC85**/**R-PC86**), so there is no unpaid copy to strand. **What the audit must map**:
      every reader and writer of a template-linked Projected row (the census at C18 round 9 is a
      start: generation, the maintain pass, the status seam, carry-forward, statement match's
      container rows, the grid, envelopes and the purchases an envelope row carries, card paybacks,
      the forecast and `/savings`); where an envelope's purchases attach when its occurrence is not
      a row; how the balance arc's transfer work (`balance:X-bi-6`, status in one row) meets it.
      **HYPOTHESES FOR THE AUDIT, NOT RULINGS** (the lane's option text asserted more than was
      measured): (1) it deletes R-PC97's stopgap and R-PC95's guard for rows a schedule names --
      plausible, because no unpaid copy exists to revert or restore; (2) it deletes what
      R-PC88/R-PC90/R-PC91/R-PC93 police -- DOUBTFUL as stated: moving the books past an UNRECORDED
      occurrence would drop it from the plan at once, which is R-PC88's REJECTED "delete it with the
      move" (the forecast rising at once), so R-PC88's refuse-first question may survive, COMPUTED
      from the schedule rather than read off rows; (3) R-PC96 judges rows NO schedule names (a
      rule-less item's rows, a carried-forward leftover) by their own day, and those stay stored
      under this model, so R-PC96 may survive too. Hand-added rows' own books gap is **PC-519**, a
      separate row. Closes **REC-534**, **REC-535**, **REC-536**, filed with this step as their
      owner at `pay_calendar:C18-a`'s tick (the leaf it now waits on) beside **PC-519** and the
      rulings above.

- [ ] **R16-c -- the PAST and the FUTURE become ONE event STREAM**, the DECOMPOSED parent of two
      leaves (**R-R90**, 2026-09-19): the MERGE first (c-1, a pure restructure), then the CALENDAR
      (c-2, the money move). Ticks with its last leaf.

- [x] **R16-c-1 -- the MERGE.** `c88ed6ba` -- as built: ONE builder, ONE replay seeded at the
      origination, ONE record type (`PaymentOutcome`); no projected event before a recorded fact; a
      pass replays the facts visible by its `as_of` (**R-R91**); the RESET arm clears standing
      charges (**R-R72** (2)); `projection_seed`, `fold_forward`, `LoanPaymentSplit` deleted;
      byte-identical on the 09-19 clone (3,952 + 919 harness lines, 0 diff). Closed **D61**,
      **balance:N-180**. Record: `historical/recurrence_r16c1_as_built_2026-09-20.md`.

- [ ] **R16-c-2 -- the CALENDAR: every contractual installment charged, from origination, in the one
      stream.** **MOVES POSTED MONEY, OWN PR, OWN RELEASE**; **R-R89** (the accrual period is the
      contract's interval) and **R-R72** (1)+(2); D53's past half; closes **D55**.

**D53 is answered at `R16-b-2` and this step inherits the answer** -- the CONTRACT charges every
forward period (**R-R37**), repealing "an overdue slot with no record ... holds flat" (B-9) for the
FUTURE half, which ruled what an unpaid installment PAYS and never what an unpaid month CHARGES.
What this step owes is the same rule for the PAST. **MOVES POSTED MONEY, OWN PR.** It also owes
**D55** (the accrual period is the CALENDAR month where the contract's is the installment month --
`$1,629.94` on which side of a boundary an extra payment falls); **D54** closed at `R16-b-2`, whose
contract calendar never charges a slot the seed charged.
**It applies R-R72 parts (1) and (2) to the settled walk**: the walk charges only the months it saw
paid (`loan_ledger._charges.charges_for_due_dates`, D53's past half), so a read AT `as_of` omits a
skipped month's interest until the catch-up payment where the read after it carries it; the one
stream charges every contractual installment after the loan's latest assertion, and an assertion
clears the charges standing before it -- the reset arm `_replay.py` applies since R16-c-1
(`c88ed6ba`), pinned on a hand-built stream and unreachable until this step charges every
installment.

**What it owes, as decomposed at R16-c-1's handoff (rulings R-R72 (1)+(2), R-R89, D53's past half,
D55):**

1. **The ONE contractual calendar on the leaf**:
   `rate_period_engine.installment_dates(origination, payment_day, through)` =
   `first_installment_date` then `_advance_one_month` (re-clamped to `payment_day` each month --
   `_plan._charge_dates` extends with `add_months` from the last contractual row, which DRIFTS after
   a February for a due day of 29-31; the one producer fixes that). `loan_event_stream` charges
   every installment from origination through the last recorded fact; `merged_stream` extends the
   same sequence through the plan's horizon (the `_PAYOFF_EXTENSION_MONTHS` rule and the
   matured-loan `through`). `charges_for_due_dates` becomes
   `charges_for_installments(dates, periods, escrow_lines)` (no month collapse); `installment_slot`
   DELETED from the leaf.
2. **Delete the partition and the projected charges**: `_plan._seed_boundaries` / `seed_slots` /
   `_charges_for`'s exclusion, `LoanEventStream.projected_charges` and the `_PROJECTED_CHARGE` kind
   (the plan then carries NO charges of its own: `LoanForwardPlan.charges` goes,
   `LoanForwardPlan.periods` goes with it once the stream's `periods` serve; the what-if extra then
   accrues at every charge on or after the projection boundary, the rule to restate). The plan's
   `last_anchor` bound on PAYMENTS (R-R72) stays: a projection due at or before the latest visible
   assertion is dropped.
3. **Every `(year, month)` key in the two tiers** (R-R89): `_estimated_from_contract`'s
   `covered_slots` -> "a record is due inside installment k's interval" (the latest contractual
   installment date at or before the record's due date); `routes/loan/_helpers._period_slot` groups
   on `charge_date` itself. `amortization_engine.schedule_dates` / `slotted_dates` stay for R16-e /
   R16-f (walk 3's).
4. **Posted money**: the settled walk's splits change wherever a month between two facts (or between
   origination and the first fact, before the latest assertion) went unpaid: the next payment clears
   the arrears first. The Van (`$14,745.51` at 5.668%, `$531.94`/mo; August 22 skipped, September 22
   paid, read September 30): today and after R16-c-1 the September fact pays `$69.65` interest /
   `$462.29` principal, balance `$14,283.22` at the read, the overdue August row pays August's
   `$67.46`, `$13,352.07` after October 22; after this step the September fact clears August AND
   September, `$139.30` / `$392.64`, `$14,352.87` at the read (`+$69.65`, the skipped month), the
   August row pays pure principal, `$13,354.27` after October 22 (`+$2.20`, September's interest on
   the un-reduced balance), and the posted September interest leg moves `$69.65` -> `$139.30`
   (computed 2026-09-19 with `accrue_monthly_interest` / `apply_payment_cash`). The release's
   migration RE-SYNCS every loan's postings (`sync_loan_postings_all_scenarios` per loan, inside the
   Alembic migration -- the backfill rule) and PRINTS per-loan the count of payments whose split
   moved and the net principal delta; the harness that reads that count is written FIRST, so the
   migration's production effect is a prediction graded before it runs rather than after. Rehearse
   up / down / up on a FRESH production clone.
5. **Harness**: extend `verify_loan_plan_sum.py` (or a sibling) to print the SETTLED splits per
   payment and the posted per-date nets, so the diff shows the ruled move and nothing else; the
   expected production move is bounded by "both live loans carry a 2026 assertion" (any skipped
   month before it is cleared by the assertion; only skipped months after it, and gaps before it
   that a later pre-assertion payment catches up, move).
6. **Tests that pin the OLD rule and need the developer's confirmation (rule 5)**: none assert "an
   unpaid month is uncharged in the settled walk" by name (grep 2026-09-19: no test module matches
   `no payment.*no charge|only the months|months it saw paid`); `charges_for_due_dates`' docstring
   states the rule and `tests/oracles/loan_monthly_composition.py:96` cites it. Expect
   `test_loan_ledger.py` / `test_confirmed_view.py` fixtures with skipped months to move; R-R72 is
   the developer's confirmation, cite it per changed value.

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
backfill does not guess. **The card's finance charge is priced by this same producer** under a
`DAILY_ACTUAL_365` member this step GAINS (rule 14: one convention table; `credit_card:CC-3i`
consumes it per constant-balance segment), and `credit_card:R-CC19` ranks that leaf AFTER this
one -- no loan step is pulled forward for the card.

- [ ] **R16-e -- walk 3 and the payment list that feeds it are DELETED** (rulings **R-R53**,
      **R-R93**; findings **D60**, **N-409**). `rate_period_engine.replay_schedule` is the THIRD
      walk over a loan: it charges a month per payment RECORD and applies the CONTRACTUAL P&I rather
      than the actual cash, and its balance has exactly ONE consumption site --
      `loan_resolver._payoff._build_forward_inputs`'s
      `replay.balance_as_of if confirmed_view is None` -- which every production read bypasses since
      plan step E1d-b. On a due-month collision it differs from walks 1 and 2 by one month's
      interest, pinned by mechanism in
      `test_biweekly_due_month_collision_reconciles_and_only_row_dates_differ`. What survives its
      balance is two CALENDAR scalars that need no walk; the walk goes, after `R16-c` has made the
      past and the future one stream. Deletion; `$0.00` on production, which never reads it.
      **Widened 2026-09-23 by R-R93, when `balance:X-au-g-2c-3b-3` was WITHDRAWN into it:** in the
      same act the step deletes the whole list that exists only to feed walk 3 --
      `LoanContext.payments`, `LoanInputs.payments`, `get_payment_history`,
      `prepare_payments_for_engine` (the escrow-subtraction FLOOR, **N-409**:
      `amount - min(escrow, amount - contractual_pi)`, a SECOND allocation rule that reports a short
      payment as exactly on schedule where the fold's `apply_payment_cash` takes the full escrow),
      `compute_contractual_pi`, `LoanContext.contractual_pi` and `PaymentRecord` -- and the list's
      two yes/no readers re-read the one stream: a retired loan with a confirmed payment
      (`balance_at/_loan_figures.py`) and the payoff calculator's `has_plan`
      (`routes/loan/calculators.py`). Measured at the withdrawal on `6de21895`: `project_forward`
      has taken no payments since `R7d-g-3`, the feed's AMOUNTS have had no reader in `app/` since
      `a1c5082f` deleted `_build_monthly_override`, and a full suite with the amounts stripped
      failed only the one test known to read them; until this step ships the floor stays in the
      code, read by nothing. `compute_contractual_pi` has one call site (`_context.py`), which
      passes `date.today()`, so its deletion takes a process-clock read off the loan context
      (**R-IJ**'s direction). `$0.00`. **Unruled, for this step's session** (the balance lane's
      trace): (a) with the list gone, `amortization_engine.slotted_dates` and `schedule_dates` have
      no production caller (today only `_engine_prep.py` calls `slotted_dates`), which bears on
      **BAL-472** and `balance:X-cb`; (b) **R-BAL7**'s refusal of a dates-only feed lost its stated
      reason, the forward override, at `R7d-g-3`; (c) `loan_payment_service/_context.py` names
      **N-409**'s owner as `balance:X-au-g-2c-3` twice, in the docstring at :79-80 and the comment
      at :222 -- both false since `R-R93`, and both go with this step's deletion of
      `contractual_pi`.
- [ ] **R16-f -- walk 4 is re-expressed over the ONE replay** (ruling **R-R53**; finding **D60**).
      `amortization_engine.project_forward` is the FOURTH walk, the contractual schedule the payoff
      and what-if surfaces read. It becomes the one replay (`loan_ledger.replay_loan_events`,
      shipped at `balance:X-au-g-2c-3b-2`) run with CONTRACTUAL cash plugged in, so a contractual
      projection, a planned projection and the settled history are one fold with three cash sources
      and cannot disagree with each other. **MOVES MONEY** on any surface where the two walks
      parted; own PR, with the pre/post oracle over both live loans. Carries **REC-517**: the derive
      arm's final-installment residual (re-owned here 2026-09-11). Shrunk by R7d-g-3: the loan page
      reads the seam's plan, not the composer's committed slice, and `project_forward` has no
      `monthly_override`.
