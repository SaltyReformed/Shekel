# Coordinator handoff, 2026-09-06/07

> **HOLDING PLACE, NOT A PLAN OF RECORD.** This file exists because the findings and developer
> rulings of 2026-09-06/07 were made while the registry lane was occupied, and lived only in
> untracked files and session transcripts. It governs NOTHING. Each arc's registry pass moves its
> content into `docs/plans/` (`ledger.md`, `steps.md`, `rulings.md`), and
> **this file is deleted once consumed**. Where it disagrees with `docs/plans/` or with the code as
> committed, they win.

## Coordinator handoff -- session `shekel-7a`, 2026-09-06

**You are picking up the COORDINATOR lane.** Josh's brief: open, order and merge every PR; check for
conflicts and decide whether a lane or you resolves them; clean up worktrees and branches; keep the
arcs from interfering; verify lanes are following `docs/plans/steps.md`; hold planning-id and
registry-interval reservations; and **keep the other sessions building rather than waiting on CI**.

Everything below was measured, not remembered. Re-verify anything you are about to act on -- this
file is a snapshot and lags the tree the moment it is written.

---

### SUPERSEDING UPDATE -- 2026-09-06, late evening (same session, after a context clear)

**Sections 1 and 4 below are STALE. This block replaces them.** Everything else in the file still
stands except where an inline correction says otherwise.

#### Current state

| | |
|---|---|
| `origin/main` | `998886b0` -- **PR #285 MERGED**, the release is live |
| `origin/dev` | `998886b0` -- identical to main (post-release resync) |
| open PRs | **#286 only** -- `feat/pay-calendar-c14e-3` -> `dev`, CI running |
| `Build & Publish Docker Image` on `998886b0` | **SUCCESS** -- `:latest` built and signed |
| production deploy | NOT DONE -- the operator's separate `shekel-deploy.sh` step |

Authority: the developer confirmed directly that this session holds
**commits, pushes, PRs, merges and registry IDs**.

#### THE INCIDENT: merging the release DELETED `dev`, and silently retargeted a money PR at production

**Read this before opening any release PR.**

`delete_branch_on_merge: true` on this repo. PR #285's head branch was **`dev` itself**, so merging
it DELETED `dev` from the remote. Restored within about a minute by fast-forwarding local `dev` to
`origin/main` and pushing; no work was lost and every lane branch was intact.

**The deletion was not the dangerous part.** While `dev` did not exist, GitHub
**SILENTLY RETARGETED open PR #286 from `dev` to `main`** -- a PR carrying `C14-e-3`, which MOVES
MONEY. No warning and no notification: the base field simply changed, and a merge would have put it
straight into production without it ever touching dev. Caught on a routine state check and set back
with `gh pr edit 286 --base dev`. It was the only open PR.
**A retargeted base is invisible in a diff.**

**ROOT CAUSE is a doc-versus-practice divergence, not the setting.** `CLAUDE.md`'s Git Workflow says
*"To ship `dev` to `main`: open a PR `dev` -> `main`"*, and that is what was followed. But the
PREVIOUS release did not do that: PR #263 was **`release/2026-09-05` -> `main`**. The practiced flow
uses a dated release branch; the documented flow does not, and the documented one is incompatible
with `delete_branch_on_merge: true`.

**RECOMMENDATION put to the developer, not yet ruled:** cut every release PR from a
`release/YYYY-MM-DD` branch off dev, and change CLAUDE.md's Git Workflow to say so. That makes
deleting `dev` UNREPRESENTABLE rather than something a coordinator must remember to undo, and keeps
auto-delete on for feature branches (the branch-cleanup story depends on it). Turning the setting
off would fix one symptom and create branch accumulation. A "restore dev after a release" checklist
line would be exactly the fence this project's doctrine says to delete.

#### Lanes, and what each is on

| lane | step | state |
|---|---|---|
| salary | `S3-e-1` | HOLDS the registry lane. Re-derived on `998886b0`; plan gate 320 passed. Final suite running. Two questions going to the developer directly: the S3-e-2 fork, and `implementation_plan_salary.md`'s cap (at 300 of 320 after trimming). |
| bank_import | `X-go` | Review returned SAFE. `origin/feat/x-gi-2` at `20f96d90`. Second in the registry queue; has written NOTHING to `docs/plans/`. |
| balance | `X-bv` door leaf | Developer ruled its fork directly. Building code-only on a FRESH branch (`refactor/balance-x-bx` is stale -- its remote was auto-deleted when #282 merged). Fourth in the queue. |
| pay_calendar | `C14-f` door fix | Unblocked: the developer ruled FORK 1 = fix the door, the sub-fork = **CONTINUE (dispatch)**, and fork 4 = pull C8's door half into C14-f. `PC-498` is NOT on its critical path. |
| recurrence | -- | DORMANT. Its next step `R5` is rank #38, blocked behind `balance:X-bi-4`. `shekel-r7dd` parked and clean. Nothing to do. |

#### The registry queue (one PR touching docs/plans/ at a time)

1. **salary** -- HOLDING
2. bank_import -- queued, drafting outside `docs/plans/`
3. **coordinator** -- C14-e-3's tick. The WIP patch `wip/pay-calendar-c14e-3-registries` @
   `4a0c48ef` carries **309 steps / 173 open, 111 edges over 91 rows, 76 legal now**, computed
   before salary's split existed. Salary's tree reads
   **311 / 175, 112 edges over 92 rows, 75 legal**.
   **Re-derive after salary's pass lands; never take the WIP text's numbers.**
4. balance -- not ready

#### Worktrees reaped this session

Two abandoned scratchpad worktrees, 172 MB (`465bf868.../devbase2`, `9c86b6a5.../base`). The third
(`01d1c159.../base-c2030826`) was left alone: it had **31 open file descriptors**, so its session is
live. The liveness test that settles it is `/proc/*/cwd` plus `/proc/*/fd/*`, not mtime.

#### Open with the developer

1. The `release/YYYY-MM-DD` recommendation above, and its CLAUDE.md edit.
2. Whether to run a neutral adversarial review on **N-548 part 1** before it is committed
   (Definition of Done 8 wants one; this session is configured not to spawn subagents unless asked).
3. Salary's two, which salary is raising itself.

#### N-548 -- part 1 BUILT, uncommitted, in an isolated worktree

Ruled: gate NEW migrations AND triage the existing 24. Built on branch `tooling/n548-migration-lint`
in this session's scratchpad, touching no lane's files. Two files:
`tools/pylint/migrations.pylintrc` and a `pylint-migrations` hook in `.pre-commit-config.yaml`.

**Three corrections to the ruling's inherited premise, all measured:**

- `C0103` fires **FIVE** times, not four -- the MODULE NAME is flagged too, because an Alembic
  filename leads with the revision hash.
- **Two further runtime proxies nobody had named**: `alembic.context` (8 `no-member`, all in
  `migrations/env.py`) and `sa.func.*` (21 `not-callable`).
- `sa.func` does NOT yield to `generated-members` -- that option governs `no-member`, and
  `not-callable` is a DIFFERENT check. It took `ignored-modules=sqlalchemy.sql.functions`, scoped to
  the one module holding `_FunctionGenerator`.

The rcfile **NARROWS** rather than disables: `invalid-name` and `no-member` stay armed, with only
the four Alembic globals (`good-names`), the revision-hash filename shape (`module-rgx`) and that
one SQLAlchemy module exempted.

**Firing proof, because a green gate may be measuring nothing:** a synthetic migration carrying one
`Decimal(0.1)` and one `.name == "Checking"` tripped `W9901` and `W9902` and the gate exited 4
(blocked). Probe deleted.

**Blast radius on the lanes -- exactly 4 files of 182 would BLOCK if edited:** `migrations/env.py`
(1 missing `Pylint:` rationale), `c4f0a5b71e83` (1 the same), `c67773dc7375`
(**2 genuine `shekel-refname-compare`**), `a9d3c15e7f42` (5 `missing-kwoa`, a FALSE POSITIVE --
pylint cannot see through `_contradiction_sql(**arm)`; the real caller at `:487` passes all five).

**PART 2's PREMISE DOES NOT REPRODUCE.** The "24 ref-table name comparisons" is **2** by the checker
and **64** or **160** by grep depending on the filter. Nothing lands on 24. Triage cannot start
until the census is DEFINED -- what counts as a ref-name comparison inside a SQL string. That
definition is a design question, not a counting chore.

---

#### 1. State at handoff

| | |
|---|---|
| `origin/dev` | `2bce6a90` (Merge PR #284) |
| `origin/main` | `9de30bce` (2026-09-05 release, PR #263) |
| dev ahead of main | **70 commits, 21 shipped steps** |
| open PRs | **#285 only** -- `dev` -> `main`, the release |
| remote branches | `dev`, `main`, `feat/pay-calendar-c14e-3`, `wip/pay-calendar-c14e-3-registries` |

**#285 is the release PR I opened but deliberately DID NOT MERGE.** See section 4.

##### Live sessions (from `ListAgents`, the only authoritative roster)

`salary`, `bank-import`, `balance`, `pay-calendar`, plus you.

**Do not census sessions with `pgrep`.** Mine missed the salary session entirely -- it runs on a
socket and did not match. `ListAgents` is the census; `pgrep` is a sampler.

##### Worktrees

| worktree | branch | state |
|---|---|---|
| `Shekel` | `dev` | the coordinator checkout (yours) |
| `shekel-c14e3` | `feat/pay-calendar-c14e-3` @ `ed267298` | **ORPHANED** -- see section 3 |
| `shekel-c14f` | `feat/pay-calendar-c14f` @ `4a0c48ef` | live; 1 untracked (`docs/design/pay_calendar_from_scratch.md`) |
| `shekel-s3e` | `feat/salary-s3e` | live; **10 dirty** (S3-e in progress) |
| `shekel-xgi2` | `feat/x-gi-2` @ `210aae08` | live; **3 dirty**; has merged dev post-#284 |
| `shekel-xbx` | `refactor/balance-x-bx` | balance stood down; **4 dirty** -- see section 3 |
| `shekel-x2b2c` | `fix/balance-x-f3c-2b-2c` | parked, clean (rank #17, blocked) |
| `shekel-s3f` | `feat/salary-s3f` | parked, clean (S3-f OPENS ON A STOP) |
| `shekel-r7dd` | `feat/r7d-f` | parked, clean; **recurrence lane is dormant** |

I reaped `shekel-c14e` (merged #280), `shekel-coord` (stale), `shekel-s3d` (merged #283), and the
branches `feat/pay-calendar-c14e`, `feat/x-gi-1`, `feat/salary-s3d`. GitHub auto-deletes remote
branches on merge, so the remote needs no cleanup.

**All eight project worktrees now have a `.venv` symlink** to `/home/josh/projects/Shekel/.venv`
(five were missing it; `scripts/test.sh` resolves `pytest` from PATH and fails on psycopg2 without
it). `.venv/` is gitignored at `.gitignore:14`.

---

#### 2. Shipped this session

- **#283** `salary:S3-d` -- a paycheck is priced per PAYDAY, no horizon parameter. pylint 10.00/10
  exit 0; 13203 passed; plan gate 320. Merged to `7c9b4dd2`.
- **#284** `bank_import:X-gi-3` -- the queue's model has no reader; 33 files, +738/-2958. pylint
  exit 0 with an **empty message list**; 13160 passed. Merged to `2bce6a90`.

---

#### 3. Loose work that has no owner -- THE MOST IMPORTANT SECTION

##### 3a. `pay_calendar:C14-e-3` is ORPHANED and it MOVES MONEY

Committed at `ed267298`, pushed to `origin/feat/pay-calendar-c14e-3`, **never merged**. The session
that built it no longer exists (it was re-tasked to C14-f, then to an arc re-plan).

What I verified, so you need not redo it:

- It **merges cleanly into current dev**:
  `git merge-tree --write-tree origin/dev origin/feat/pay-calendar-c14e-3` exits **0**.
- The merge is **semantically** right, not just textually clean. In the merged
  `app/services/balance_at/_context.py`: pay_calendar's rename is present
  (`registration_service.register_user`, 1 hit), the stale `auth_service.register_user` is **gone**
  (0 hits), and salary's `def paychecks` **survives** (1 hit).
- It is **code-only** -- zero planning documents.

**What it still needs: a suite run on the merged tree.** A clean textual merge is not evidence about
semantics.

Its registry pass is a separate pushed branch, `wip/pay-calendar-c14e-3-registries` @ `4a0c48ef`
(562-line patch, plan gate 320 passed, rumdl clean). **WIP, not for merge as-is** -- three derived
numbers (309 steps / 173 open, 111 edges over 91 rows, 76 legal-to-start-now) must be re-derived
from the plan gate on the merged tree.

**I asked the pay-calendar session to shepherd it and it correctly declined**: Josh scoped that
session to C14-f and then to a re-plan, and merging another lane's deliverable is neither.
*A peer asking is not the developer scoping.* Do not repeat my mistake. This needs Josh to assign
it.

##### 3b. `balance:X-bv` uncommitted work in `shekel-xbx`

Balance stood down after Josh ruled doors-first. Uncommitted there:

- `app/models/transaction.py` (modified -- the `__table_args__` split **plus** the constraint)
- `app/models/_transaction_table_args.py` (new)
- `migrations/versions/b4d9e1c7a052_...py` (new) -- **MUST NOT SHIP**
- `HANDOFF-X-bv.md` (untracked, 261 lines, 9 sections -- read its s6 and s8)

**Backed up in this directory** (`.coordinator-handoff/`): the tracked diff as
`ORPHAN-xbx-balance-x-bv.patch`, both untracked files under `ORPHAN-xbx-untracked/`, and a copy of
the handoff. The migration copy is renamed `.py.DO-NOT-SHIP` so alembic cannot mistake it for a live
revision (verified: `script_location = migrations`, and `b4d9e1c7a052` is absent from the live
`migrations/versions/`).

---

#### 4. Release PR #285 -- open, CI running, NOT merged

I opened it under Josh's standing approval ("cut it after salary S3-d merges").
**I did not merge it**, because merging is the outward-facing step and the dump question is his.

Merging to `main` triggers `docker-publish.yml` (builds and signs `:latest`). The production deploy
itself is a **separate operator step** (`shekel-deploy.sh`, digest-pinned).

**Three migrations ship** and run automatically -- `entrypoint.sh` -> `scripts/init_database.py:221`
-> `command.upgrade(alembic_cfg, "head")` on **every container start**, unconditionally:
`a1c7e5d20f43`, `c9a4e17b53d8`, `d4e8b1c62f07`.

**I checked the known deploy-breaking shape and it is CLEAR**: a migration that INSERTs a row into
an *existing* ref table breaks the boot, because `create_app()` eagerly runs `ref_cache.init()`,
fatal on a missing row though it tolerates a missing whole table. None of the three inserts a ref
row (`d4e8b1c62f07` only *reads* `ref.raise_types` as a join at line 173).
**Neither CI nor the dev container reproduces that failure mode**, so this was established by
reading the migrations, not by a green run. A row-adding migration must be verified by cloning prod
to a throwaway DB and running `FLASK_ENV=production python scripts/init_database.py` against it.

**Tell Josh to take a Postgres dump before deploying.** It is the recovery mechanism, not a
formality. `shekel-deploy.sh` **never runs `flask db downgrade`**; it refuses the repin and says a
genuine downgrade means restoring a dump first. Two consequences of that one property:

1. *Favourable*: no migration-downgrade defect can reach production -- `balance:X-bw`'s `-$9,677.29`
   round-trip defect lives only in the operator's manual path.
2. *Unfavourable*: the rollback re-pins the **image** and leaves the schema where the migration put
   it. A deploy that migrates then goes unhealthy leaves **old code on the new schema**, and a
   successful-looking rollback is **not** evidence the database was restored.

---

#### 5. Open with Josh -- nothing else blocks, these do

1. **Merge #285 + take the pre-deploy dump.**
2. **Who lands the orphaned C14-e-3** (section 3a). Highest value already spent, nobody accountable.
3. **`__table_args__` standalone split** -- balance's evidence: `transaction.py` 1011 -> 662 with
   the sibling at 384, both 10.00/10 with custom checkers armed, constraint inventory diffed against
   HEAD **identical** (11 indexes, 1 unique, 3 FKs unchanged, CheckConstraints 9 -> 10, nothing
   lost). Unblocks the 1000-line ceiling. It is unasked-for scope, so it is his call.
4. **`bank_import:X-gi-2a`** -- the MATCH pane draws two controls for one money decision; two
   different outcomes submit an identical body. Ruling stays **in the bank-import session**, where
   the trace lives (he chose that). `X-go`, `X-gp`, `R-BI2` reserved for the outcome.
5. **Migration-lint reshape (N-548)** -- see section 7; my first recommendation was measured
   unbuildable and he ruled on the bad description.
6. **Memory-index eviction** -- the index is FULL: `24464 B / 24473 B, 9 B remaining`. Any new
   memory evicts one. Which one is his call, not a session's.

**Do NOT carry the C14-f predicted-day anchor fork to him.** He refused its premise and ruled "stop
and re-plan the pay_calendar arc". It is moot.

---

#### 6. ID register -- reservations I issued; honour them

Full detail in `ID-REGISTER.md` beside this file. Ceilings measured on dev.

| arc | next ruling | next ledger |
|---|---|---|
| balance | `R-BAL6` (reserved, unminted) | `BAL-470` (reserved) |
| bank_import | `R-BI2` (reserved) | `BI-481` (reserved) |
| pay_calendar | `R-PC63` | `PC-507` (it used PC-501..506) |
| recurrence | `R-R63` | `REC-516` |
| salary | `R-SAL14` **(issued, #283)** | -- |
| credit_card | `R-CC13` | -- |
| shared | -- | `N-548` **(held for the migration-lint finding, owner `developer-decision`)** |

- **`N-` is a SHARED run across arcs.** Never let a lane self-assign one. `N-547` went to salary.
- **A new ruling carries its ARC'S PREFIX** (developer, 2026-09-05). The shared LETTER run
  (`R-JA`..`R-KD`) is **closed**; nothing new joins it.
- **TRAP: `R-BAL130` is PROSE, not a minted id.** It appears only at `rulings.md:17` as the
  counterexample the convention warns against. A naive max-grep hands you `R-BAL131`. Only
  `R-BAL1`..`R-BAL5` exist.
- Step ids `X-go` and `X-gp` reserved for bank_import (X-gn is the ceiling of the `X-g` run).
- **Never grep sibling worktrees for a free id** -- a worktree created after you look is invisible.

---

#### 7. Standing rules I established -- keep or revise deliberately

1. **Registry sequencing.** Only ONE registry-touching PR (`steps.md`, `ledger.md`, `rulings.md`) in
   flight at a time; the coordinator sequences by **readiness**, not by arc. Code-only PRs flow
   around it freely and in parallel.
2. **The owning lane re-applies its own registry patch on the MERGED TREE**, taking every derived
   number from the plan gate rather than its own counting.
   *I originally told lanes to hand me row text; that was wrong* -- open/edge/ready counts are
   derived, and text computed against a lane's own base carries stale numbers that merge with no
   conflict, reintroducing the very silent-merge defect the rule exists to stop. pay-calendar caught
   it.
3. **Land code first, registry second**, when the registry lane is busy. Now the house pattern.
4. Lanes push their own branches freely (CLAUDE.md permits it); they do **not** open or merge PRs.

##### The migration-lint finding (N-548), reshaped

`migrations/` **is linted by nothing** -- `.pre-commit-config.yaml` scopes pylint to `^app/`,
`^scripts/`, `^tests/manual/`, `^tools/pylint/shekel_checkers/`, `^(app|scripts)/`, `^tests/`; CI's
six lint steps never target it; the Stop hook runs `pylint app/`.
**181 files, all nine custom checkers blind.** Measured: **24** ref-table name comparisons in
migration SQL, **0** `Decimal(<float>)`.

**It is a COVERAGE finding, not 24 defects.** A migration is frozen at its own revision and may not
import `app.enums`, so ids-for-logic is genuinely unavailable; some of the 24 are reasoned
tradeoffs. Nobody can say which were reasoned and which were never looked at.

**My recommendation to Josh was measured unbuildable by pay-calendar, after he had ruled on it.**
`pylint` on a correct shipped migration rates **0.00/10**: `C0103` x4 on `revision`,
`down_revision`, `branch_labels`, `depends_on` (**mandatory** lowercase names -- Alembic's loader
reads them by name) and `E1101` x3 on `alembic.op` (a runtime **proxy**; pylint resolves no member
on it). `E1101` is an `E` and the gate is `--fail-on=E,F`, so "lint the tree" means
**all 181 files hard-fail and each needs disables** -- 181 frozen artifacts edited to satisfy a
checker's inability to understand Alembic.

**The shape that works** (smaller, not bigger): a scoped rcfile neutralising only the two Alembic
false positives with all nine custom checkers **armed**, plus `files: ^migrations/.*\.py$` in
pre-commit -- which passes only CHANGED files, so it gates every NEW migration and touches **zero**
of the 181. The existing 24 then stay separately decidable. **Josh has not ruled on the reshape.**

---

#### 8. Findings held for lanes -- these have no other home

- **`salary:S3-e`'s census OMITS `AccountPayrollFeed.prices()`** (zero mentions in `steps.md` and
  `implementation_plan_salary.md`). Its one live reader is `investment_projection/_inputs.py:517`,
  gating `_average_transfer_contribution` -- a **transfer** question, not a deduction one. If S3-e
  redefines it as "the pricer answered" it returns True everywhere, the average is never added, and
  **an account's whole recurring-transfer stream disappears from the forward walk for the entire
  horizon**, for every account funded by both a deduction and transfers. It must survive as a
  **calendar** question (`calendar.horizon()`). **Fold into S3-e's specification.** Salary reported
  this as it cleared context; it is not in any registry.
- **bank_import owes a registry pass** (not yet done): tick X-gi-3, close BI-479/BI-480 with
  BI-479's remedy text **corrected**, apply the **"four -> three" `ReviewSet` members** fix, file
  BI-481 (the R-IV docstring names `X-gn`; the step is `X-gi-2a`). Until it lands,
  **X-gi-3 reads as unticked in `steps.md` while its code is on dev** -- do not read tick state as
  truth.
- **Two bank_import rows need ids**: (1) five more orphans of the X-gi-2 deletion --
  `ReviewBounds.calendar_opens` plus `MatchProposal.bank_amount`/`.app_amount`/`.confirms`/
  `.redate_gap`, four behind live tests, BI-480's exact shape; (2) the sweep guard's staging
  regressed from three withholding arms to one, where three existed because a review ruled two
  insufficient.
- **`balance:X-bv` corrections**, riding with X-bv-1 whenever it lands: "the equivalent" is wrong at
  `ledger.md:34` and `README.md:1036` -- **TWO sites, not three**; `steps.md:70` carries the same
  misleading framing without the phrase and is being re-specified anyway. Also: the tree holds
  **three** per-kind cutovers and **two** run bare; `c9a4e7b21d58` is the one BAL-463 never names.
- **pay_calendar re-plan**: `budget.pay_periods` is a **materialised cache of a function**. Measured
  read-only on prod: 63 rows for user 1, **63 of 63 exactly reproducible** from the schedule row's
  opening and cadence, zero departures; the forward half is a pure function of
  `(rhythm, rolling_target_periods, today)`. **699 of 928 live transactions** are filed against a
  future period whose only justification is that someone materialised a projection. Write-up:
  `docs/design/pay_calendar_from_scratch.md` on `feat/pay-calendar-c14f` (in `docs/design/`, which
  the plan gate does not grade; touches no registry; asks for ids rather than taking them).
- **Carry to other lanes, from pay_calendar**: `C9` (#47) is **priced** at `+$2,501.92` Empower and
  `+$5,427.07` Property at six months, because the fold walks SAVED periods rather than the
  calendar -- the materialisation boundary reaching money (existing finding, quoted not re-filed).
  `C8` (#46) already owns half of P80's real remedy.
  **RETRACTED 2026-09-06 by pay_calendar, which found its own number wrong**: the "classified
  2026-08-11, predating P80" claim INVERTS. That date is real prose but is not the classifying
  act -- `R-PC50` classifies C8 and is dated **2026-09-03**, while P80 was found **2026-09-01**, so
  the classifier had P80 in hand. Only the rank observation survives: C8 holds half of P80's remedy
  at #46 while P80's owner sits at #13.
- **DO NOT FILE**: `journal_entries.pay_period_id` is `ON DELETE CASCADE` onto a table whose
  cache-maintenance doors delete rows, **but TWO mechanisms fence it**, not one (sharpened
  2026-09-06): `pay_period_locks._period_ids_with_unbalanced_ledger` fences the lock path, AND
  `reset_pay_periods` does not call the classifier at all -- it lets the entries cascade and
  RE-DERIVES them from their source facts. **Not a live bug.** Most likely thing to be rediscovered
  and filed in good faith.
- **`PC-501` is owned by `developer-decision`**, not by pay_calendar; `BA-06`'s
  `MAX(start_date) + (cadence_days - 1)` horizon is a THIRD spelling of the end rule and is
  deliberately untouched under rule 6. No separate id -- it is PC-501's own subject.

---

#### 9. Traps this session paid for -- all reproduced, not repeated hearsay

1. **A PIPED EXIT CODE REPORTS THE LAST COMMAND.** Balance ran the suite through `| tail -40`, the
   harness said "exit code 0", and **the suite was red the whole time**. Reproduce it:
   `( exit 7 ) | tail -1; echo $?` prints **0**; only `set -o pipefail` recovers the 7.
   **Read the summary line.** My CI watchers pipe through `tail`, so their exit codes are
   worthless -- but neither merge rested on one; both rested on `gh pr checks` printing `pass` and
   `gh pr view --json mergeStateStatus` returning `CLEAN`, read directly.
2. **WRITING THE CITATION IS THE CHECK.** Balance asserted "three files" from a first read and
   repeated it across three messages; it only broke when it had to write `file:line` into a handoff.
   A summary would never have caught it. (I verified: two, not three.)
3. **A GREP OVER A DOC THAT DISCUSSES ITS OWN IDS READS PROSE AS DATA.** `R-BAL130` -- see s6.
4. **`zsh` ate `:a` as a path modifier** in `git show "$tree:app/..."`, producing empty greps that
   looked exactly like "the merge dropped both sides". Use `"${tree}":"${f}"`. A shell artifact can
   imitate the silent-merge failure you are hunting.
5. **A CLEAN MERGE IS NOT A SEMANTIC ONE.** Check that both sides' changes survive by name, as I did
   for `_context.py` (s3a).
6. **`git diff` DOES NOT CAPTURE UNTRACKED FILES.** Backing up balance's tree needed a separate copy
   pass; one of the two untracked files was an entire new module.
7. **A SCRATCHPAD PATH IS KEYED TO A SESSION UUID.** pay-calendar moved its 562-line registry patch
   into git for exactly this reason. That is why this directory exists rather than a scratchpad.
8. **rumdl and the plan gate PULL OPPOSITE WAYS.** rumdl reflows paragraphs to fill 100 chars; the
   plan gate caps a SHIPPED step's entry at **6 lines**. Reflowing pushed an entry 6 -> 7 and failed
   the gate. **Shorten the prose; do not re-wrap.** And a green plan gate is **not** sufficient
   before a docs commit -- rumdl is a separate hook.
9. **READ A CAP FROM `tools/plan_gate/test_arc_documents.py`'s `CAPS`, NEVER FROM PROSE.** The
   pay_calendar arc-document cap is **520**, not the 500 I relayed to Josh from a lane's undated
   note (raised at `1a4f3af8`). An undated measurement quoted as a *reason* decays invisibly.
10. **A ledger row near the 2,000-char cap CANNOT ABSORB A RE-POINT** -- the gate refuses the
    commit. `N-496` sat at 1,989 and had to be compressed to exactly 2,000.
11. **Rule 5 archiving frees BODIES ONLY** (~six lines for C4+C13, 500 -> 494), because rule 12
    requires every indexed step to keep a checkbox entry in **both** directions. And a live sentence
    may not depend on an archived one.
12. **A DUPLICATE CLASS OR METHOD NAME UNDER `tests/` SILENTLY SHADOWS** the earlier definition --
    never collected, assertions never run. **Nothing gates it** (pylint is scoped to `^app/`).
    bank_import swept all 491 test modules: no other instance. Cheap checker while the tree is
    clean.

---

#### 10. Suggested first moves

1. `git fetch origin --prune`, then re-read section 1 against reality.
2. Check **#285** (`gh pr checks 285` -- read it directly, never a piped status). If green, it still
   needs Josh's go plus the dump before merging.
3. Ask Josh for the six decisions in section 5 -- **C14-e-3's owner first**, it is the one with
   value already spent.
4. Message each live lane to re-establish contact: `ListAgents` for the roster, then `SendMessage`.
   Tell them who you are and that the registry lane is currently **empty**.
5. Do **not** reap `shekel-xbx`, `shekel-c14e3`, `shekel-c14f`, `shekel-s3e` or `shekel-xgi2` -- all
   hold live or orphaned work.

**`SHEKEL_PR_COORDINATOR=1` must be set in your launch environment** for
`gh pr create`/`gh pr merge` to bypass the guard prompt. It is a LAUNCH env var, never a `Bash`
export.
