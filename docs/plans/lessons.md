# Process lessons

**One copy of what this project has already paid to learn, shared by every arc.** They lived in the
balance README's section 8, which is why three of the four arc documents did not have them -- the
same denormalization `conventions.md` exists to remove, on the one artifact whose whole value is
being read before the mistake is repeated.

**One line each, and the line is the LANDMINE, not the story.** A lesson earns its place by having
COST something -- a rebuild, a wrong figure in production, a review round -- and it names the step
or finding that paid, so a reader can go and check. The narrative of what went wrong stays in that
commit: this file only works if it is read straight through, and prose is what stops that happening.

**This document IS capped (rule 4), and it has two ways back under the cap.** CONDENSE a lesson that
has grown into a paragraph, and RETIRE one that has been MECHANIZED into a gate -- its line moves to
that gate's own rationale, where a reader meets it at the moment it fires. An append-only file
nobody finishes reading loses its lessons just as completely as deleting them would.

What this file must never become is a place to record STATE -- what shipped, what is next, what
production runs. That is `steps.md`'s, and a sentence about it here would be a second answer beside
no reconciler.

## The rule itself

- **An argument a caller can get wrong is a defect, not a contract.**
- **A DRY refactor of a PREDICATE can move money.** Two spellings that agree by reading are two
  answers until one is deleted.
- **When two figures PARTITION a set, write both halves from ONE predicate**, or the boundary drifts
  and both halves look right.
- **When a rule is re-keyed, the complement must move with it**, or replay and projection stop being
  exact complements.
- **When two sides of one problem have different SHAPES, the loose side is where the next hole is.**
- **WHEN TWO VALUES HAVE ALWAYS BEEN EQUAL BY CONSTRUCTION, THE CODE THAT COUPLES THEM DOES NOT
  EXIST.** Ask which values a field has silently equalled before widening what it holds (X-f1c4c).
- **Ask what a producer says the SECOND time.** One correct on the ordinary path can be inverted on
  the correcting one, and only the correcting path is worth a test (X-f2-a, `-$45.86` vs `-$92.29`).
- **A MECHANISM THAT HAS ONLY EVER BEEN RE-KEYED HAS NEVER BEEN DESIGNED.** Ask when the rule was
  last decided, not when it was last edited.
- **When a conversion is mechanical, the DIRECTION of the type change has a mirror, and the mirror
  is where the bug is.**
- **SCORE THE RULE YOU SHIPPED, NOT THE RULE YOU DESIGNED.** Any change to a rule after it was
  scored re-opens the score; re-run the measuring script as the LAST act of the build.
- **A RULE THAT READS A CLOCK MUST BE APPLIED ONCE**, and "the writer does not trust its caller" is
  not a reason to apply it twice; carry "already checked" in a TYPE the checker alone can mint
  (X-f1e2, a midnight roll between two applications of one floor).
- **AN APPEND-ONLY TABLE NEVER LICENSES AN UNSERIALISED READ-MODIFY-WRITE IN THE SAME TRANSACTION.**
  Name the tables the transaction WRITES, not the one the ruling is about (R-EN, N-190).
- **IDEMPOTENCY IS A PROPERTY OF A REQUEST, NOT OF THE ROW IT CARRIES.** A unique index over the
  row's own values must mis-classify a retry or a re-assertion; ask which way it errs and what each
  error costs (R-EQ).
- **WHEN TWO DOORS WRITE ONE FACT, ALIGNING THEM CAN BE WORSE THAN DELETING ONE.** Ask what each
  door's SURFACE means before making them agree (X-f1e1).
- **AN IMPOSSIBILITY ARGUMENT THAT CONSIDERS ONE LOCK CLASS IS NOT AN IMPOSSIBILITY ARGUMENT.** Name
  the classes it ranges over: the deadlock ruled out advisory-vs-advisory reproduced advisory-vs-ROW
  against a real PostgreSQL (N-193).
- **READING A LAZY RELATIONSHIP IS A FLUSH**, so where a guard SITS decides which exception net
  catches the request's first UPDATE -- a guard moved for READABILITY moved a database write (X-ap).
- **A READ DOOR THAT GAINS A RAISE BREAKS EVERY REPAIR PATH THE UI ADVERTISES THROUGH IT.** When a
  reader starts validating, ask which callers were about to overwrite the thing it now validates
  (R7b-1, where the advertised remedy became a 500 with every gate green).
- **NAME A WRAPPER'S FIELD SOMETHING THE WRAPPED PRIMITIVE CANNOT ANSWER.** `ObservationDay.day`
  compiled against a raw `date` and returned the day of the MONTH into an SQL bound (X-f1e2).
- **A shared primitive reached through a private import is telling you the package boundary is
  wrong.**
- **A fail-CLOSED gate is scoped by module identity, so creating a module is how you escape it.**

## The surface a human actually sees

- **A REFUSAL THE SUBMITTING SURFACE CANNOT RENDER IS NOT A REFUSAL, and a SUCCESS that renders the
  pre-submission state is indistinguishable from a no-op.** This app's htmx config leaves 4xx
  non-swapping, so a correct 400 left the form sitting there. Press the button and watch (X-f1c4c).
- **AN AFFORDANCE THAT CANNOT SUCCEED IS DELETED, NOT GIVEN A NICER REFUSAL.** When a refusal is
  reached by an ordinary click, the question is why the click was offered (R-ET).
- **A refusal is only as good as the repair it names, and nobody had pressed the button.**
- **A FRAGMENT'S MOUNT IS PART OF ITS CORRECTNESS, AND A RESPONSE CAN DESTROY ITS OWN MESSAGE.** An
  out-of-band swap into a region that re-fetches on the event the SAME response fires is a race the
  response always loses. Ask what re-renders the target, not just whether it exists (N-199).
- **ONE SURFACE GETTING A RULE RIGHT HIDES THAT THE SHARED PARTIAL NEVER GOT IT.** Loan balances had
  been read-only on the cockpit for a year; the partial the other four surfaces include had not, so
  the rule looked shipped and was one-fifth shipped.
- **A JS visibility defect is invisible to pytest** -- drive it in a browser (X-f2-b: 8,556 green
  over a toast that reached the DOM and never appeared).

## Tests and controls that cannot fail

- **Every guard gets a negative control shown to fire, and a REPAIR for a dead control is itself a
  control needing the same mutation.** A correction can carry the defect it corrects.
- **A TEST THAT REPLACES A DELETED FEATURE'S TEST MUST BE RUN AGAINST THE REVERT.** Deleting a
  behaviour and its test lowers coverage silently unless the replacement is shown to fail without
  the change (two of X-f1e1's four controls passed on the old code).
- **GRADE THE ONE TOKEN THE FEATURE HANGS ON, OR A MUTATION WILL PROVE YOU DID NOT.** Deleting
  `data-toast-auto-show`, and separately `hx-swap-oob="true"`, each left the whole 7,867-test suite
  green over a permanently invisible acknowledgement (N-199).
- **A RESPONSE-BODY ASSERTION CANNOT SEE WHAT A SECOND REQUEST DOES TO THE PAGE.** Where a
  fragment's correctness is its POSITION, grade the position (N-199).
- **A NARROWING MUST ASSERT WHAT IT LEAVES ALONE, NOT ONLY WHAT IT REMOVES**, and **when a change
  makes a claim elsewhere in its own diff false, that claim was the test that was missing** (X-ap
  subtracted a three-member band and silently retired the ARCHIVE transition).
- **Grading a value object's ids is not grading its FIELDS.** X-f2-c1 mutated `purchased_on` to the
  statement day and left 8,671 tests green.
- **A NEW FIXTURE IS A NEW CONTROL, AND IT CAN BE BORN DEAD.** SQLAlchemy accepts an assignment to a
  field that does not exist.
- **A test whose fixture has no data cannot distinguish two producers.**
- **`hasattr` on a dataclass is not a test**, and neither is `is not None` after `isinstance`.
- **A list returned for its COUNT must have its count asserted.**
- **CONVERTING A SURFACE TO "RAISE" BLINDS EVERY TEST WHOSE FIXTURE CANNOT REACH IT.**
- **A SUITE THAT PASSES ON 353 DAYS A YEAR IS NOT A GATE**, and the day it fails it will look like
  your change.
- **Ask what a test's failure would have COST before deleting it, and write the answer down.**
- **THE STATE A GUARD DEFENDS AND THE STATE THE APP IS IN CAN BE OPPOSITES.**
- **A guard written against the wrong failure mode can still be a good guard** -- write the reason
  beside it.
- **A skip is safer to state than a fire**, when the operation being guarded is the one under test.

## Instruments, oracles and censuses

- **AN INSTRUMENT MUST BE SHOWN TO HAVE REACHED ITS SUBJECT.** One that cannot authenticate reports
  no differences, loudly and wrongly. Assert the identity a result is attributed to, not just that a
  result came back.
- **A GATE MUST BE EXERCISED AGAINST THE ARTIFACT IT GRADES, never read or tested only
  synthetically.** A pattern that matches the real file nowhere passes every synthetic control. Row
  **P52** is this lesson found live: a docstring-stated `grep` canary whose pattern is line-based
  against a call site that WRAPS, so it has always matched zero lines and "the gate passes" has
  always meant "nothing matched".
- **An ORACLE that states a different rule than the engine lets both be wrong together.**
- **AN AGREEMENT ORACLE CANNOT SEE A PRODUCER THAT READS THE VALUE IT IS REPLACING.** The control
  that sees it is INVARIANCE -- perturb the input the new rule must not be reading and require the
  answer not to move (X-au-b scored 997 of 997 replaced by `return txn.estimated_amount`).
- **A SERIES MINED OUT OF A COLUMN CANNOT INDEPENDENTLY GRADE A READER OF THAT SERIES.** Ask where
  the oracle's expected value came from, not just whether it differs from the code under test
  (X-au-a backfilled the price history FROM the rows X-au-b's 452 agreements then re-attested).
- **A BASELINE IS ONLY A BASELINE AGAINST THE DATABASE IT WAS TAKEN FROM**, and widening an
  instrument is a shape change needing the same normalization the code does.
- **Scan with an AST, not a regex -- and an AST census is a grep with better manners unless it
  FOLLOWS THE DATA.** A census and a gate can be blind the same way, and then they confirm each
  other.
- **A static guard that greps for a NAME cannot tell code from prose.**
- **A CENSUS THAT IS NOT COMMITTED IS AN UNCITED CLAIM.**
- **COUNT THE CALL GRAPH, NOT THE CALL SITES.** One finding said four spellings; the tree held 18.
- **A COUNT IN A DOCSTRING IS A CLAIM, AND THIS ARC KEEPS WRITING IT WRONG.**
- **BEFORE READING A CLEAN DIFF, CHECK THE FIGURE THE STEP IS ABOUT IS NOT NULL IN IT.** C2-f2c's
  harness came back byte-identical over 39,939 lines while `retirement_marker_index` -- the one
  figure its ledger row (**P48**) names -- was `None` on every line, because the clone's owner had
  set no retirement date. A production clone is a sample of one owner's CHOICES, not of the state
  space; where it does not exercise the subject, WRITE the state into the clone and re-capture both
  sides rather than accepting the clean run.
- **PLANT THE FIRING CONTROL AT EVERY WIRING SITE, AND CHECK THE PLANTED DEFECT IS REACHABLE ON THE
  REAL DATA.** C2-f2a's first control dropped the FIRST period from the contribution axis and moved
  `$0.00` -- every early period sits at or before the account's latest assertion, which that tier
  skips -- so the harness read as blind when it was not. The same defect at the LAST period moved
  `-$182.29` and `-$190.39` at the two sites.

## Working this plan

- **A RULING ID IS A CITATION, SO THE RULING SHIPS FIRST.**
- **REVIEW A FROZEN TREE.** Applying one review's fixes while another is running invalidates both,
  and a mutation-planting reviewer needs its own worktree.
- **A STEP WITH MULTIPLE LEAVES, MIGRATIONS AND REVIEWS SPANS SESSIONS.** Stop at the first leaf
  boundary and hand off; the alternative is a mechanical cleanup pass applied with too little care
  at the end of a long session (X-f1c3, 2026-08-04).
- **A TABLE THAT NAMES ITS OWN `HEAD` IS FALSE THE MOMENT IT LANDS**, because the commit that writes
  it moves `HEAD` past the hash it just wrote. Record the last CODE commit and the remote, which are
  stable, and tell the reader to re-measure the rest.
- **Documents rot in days here.** This file is the only one allowed to rot, and every edit re-dates
  it.
