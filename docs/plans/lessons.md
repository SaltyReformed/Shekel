# Process lessons

**One copy of what this project has already paid to learn, shared by every arc.** They lived in the
balance README's section 8, which is why three of the four arc documents did not have them -- the
same denormalization `conventions.md` exists to remove, on the one artifact whose whole value is
being read before the mistake is repeated.

**One line each; the evidence is in the commits of the step that paid for it.** A lesson earns its
line by having COST something -- a rebuild, a wrong figure in production, a review round -- and the
step that paid is named where the line would otherwise be unfalsifiable.

**This document IS capped (rule 4), and it has a retirement rule of its own.** It was uncapped until
2026-08-11 on the argument that a lesson never goes stale -- true, and beside the point: an
append-only file nobody finishes reading loses its lessons just as completely as deleting them
would. **A lesson that has been MECHANIZED into a gate stops being a lesson and its line goes**, to
the gate's own rationale where a reader meets it at the moment it fires. That is the retirement path
when the cap binds, and it is the only one that loses nothing.

What this file must never become is a place to record STATE -- what shipped, what is next, what
production runs. That is `steps.md`'s, and a sentence about it here would be a second answer beside
no reconciler.

- **An argument a caller can get wrong is a defect, not a contract.**
- **Ask what a producer says the SECOND time.** One correct on the ordinary path can be inverted on
  the correcting one, and only the correcting path is worth a test (X-f2-a, `-$45.86` vs `-$92.29`).
- **Grading a value object's ids is not grading its FIELDS.** X-f2-c1 mutated `purchased_on` to the
  statement day and left 8,671 tests green.
- **A JS visibility defect is invisible to pytest** -- drive it in a browser (X-f2-b: 8,556 green
  over a toast that reached the DOM and never appeared).
- **A DRY refactor of a PREDICATE can move money.** Two spellings that agree by reading are two
  answers until one is deleted.
- **When two figures PARTITION a set, write both halves from ONE predicate**, or the boundary drifts
  and both halves look right.
- **When two sides of one problem have different SHAPES, the loose side is where the next hole is.**
- **When a rule is re-keyed, the complement must move with it**, or replay and projection stop being
  exact complements.
- **SCORE THE RULE YOU SHIPPED, NOT THE RULE YOU DESIGNED.** Any change to a rule after it was
  scored re-opens the score; re-run the measuring script as the LAST act of the build.
- **AN APPEND-ONLY TABLE NEVER LICENSES AN UNSERIALISED READ-MODIFY-WRITE IN THE SAME TRANSACTION.**
  Name the tables the transaction WRITES, not the one the ruling is about. Ruling R-EN deleted a
  lock on "a second tab overwrites nothing", true of one table in a transaction that mutates three;
  the deleted column had been serialising the reconcile by accident. The precedent it cited carried
  the identical defect, so the mistake was made twice (N-190).
- **IDEMPOTENCY IS A PROPERTY OF A REQUEST, NOT OF THE ROW IT CARRIES.** A retry and a deliberate
  re-assertion are byte-identical by construction, so a unique index over the row's own values must
  mis-classify one of them. Ask which way it errs and what each error costs: here a false refusal
  rendered a wrong balance while a surplus append-only row posted `$0.00`, and the two are not the
  same size (R-EQ).
- **WHEN TWO DOORS WRITE ONE FACT, ALIGNING THEM CAN BE WORSE THAN DELETING ONE.** Ask what each
  door's SURFACE means before making them agree: two reviews recommended putting the account-edit
  door on ruling R-EQ's rule, and because that form PRE-FILLS the balance, a rename would then have
  asserted today's figure and absorbed two months of unreconciled purchases. A door that is not a
  balance-reading surface should not be taught to read balances better (X-f1e1).
- **A TEST THAT REPLACES A DELETED FEATURE'S TEST MUST BE RUN AGAINST THE REVERT.** Two of X-f1e1's
  four controls passed on the old code -- one submitted the exact input the deleted branch
  short-circuited, the other could not tell "unreachable" from "refused" because both answer 200 and
  write nothing, and it never asserted the part of the edit that DOES differ. Deleting a behaviour
  and its test lowers coverage silently unless the replacement is shown to fail without the change.
- **WHEN TWO VALUES HAVE ALWAYS BEEN EQUAL BY CONSTRUCTION, THE CODE THAT COUPLES THEM DOES NOT
  EXIST.** Making one of them user-supplied does not break a rule you can go and read -- it breaks
  an assumption nothing ever had to write down. Before changing what a field can hold, ask which
  values it has silently equalled: the reconcile prompt keyed on `MAX(observed_on)` was correct for
  as long as every true-up stamped today, and nothing in it named that dependency (X-f1c4c).
- **A SUCCESS RESPONSE THAT RENDERS THE PRE-SUBMISSION STATE IS INDISTINGUISHABLE FROM A NO-OP.**
  The same defect as an unrenderable refusal, on the other side, and it hides better -- a 200 looks
  like it worked. A write whose whole point is invisible on the surface that made it will be made
  twice.
- **A REFUSAL THE SUBMITTING SURFACE CANNOT RENDER IS NOT A REFUSAL.** Before adding a rule that a
  user can trip, press the button and watch: this app's htmx config leaves 4xx non-swapping, so a
  correct 400 with a correct message was invisible and the form simply sat there. Ask what the
  refusal LOOKS like, not only whether it fires (X-f1c4c).
- **A RULE THAT READS A CLOCK MUST BE APPLIED ONCE, AND "the writer does not trust its caller" IS
  NOT A REASON TO APPLY IT TWICE.** Unifying two writers put one bound on both sides of a call; the
  floor is `min(earliest period start, today)`, so a midnight roll between the two applications
  refused the day the first had just produced -- and the second refusal landed after the row was
  flushed. Ask whether a re-check is a pure function before calling it defence-in-depth; where it is
  not, carry "already checked" in a TYPE the checker alone can mint (X-f1e2).
- **A FRAGMENT'S MOUNT IS PART OF ITS CORRECTNESS, AND A RESPONSE CAN DESTROY ITS OWN MESSAGE.** An
  out-of-band swap into a region that re-fetches on the event the SAME response fires is a race the
  response always loses. Ask what re-renders the target, not just whether the target exists (N-199).
- **A RESPONSE-BODY ASSERTION CANNOT SEE WHAT A SECOND REQUEST DOES TO THE PAGE.** The test that
  missed N-199 asserted the message was in the body -- it was, and it was gone from the DOM a moment
  later. Where a fragment's correctness is its POSITION, grade the position.
- **GRADE THE ONE TOKEN THE FEATURE HANGS ON, OR A MUTATION WILL PROVE YOU DID NOT.** Deleting
  `data-toast-auto-show` left the acknowledgement in the DOM, permanently invisible -- N-199's exact
  symptom -- and the whole 7,867-test suite passed. Deleting `hx-swap-oob="true"` did too. An
  adversarial review MEASURED both. A test that grades a fragment's id, copy, day and amount and
  stops one attribute short of what makes it reach a human is grading everything except the defect.
- **AN AFFORDANCE THAT CANNOT SUCCEED IS DELETED, NOT GIVEN A NICER REFUSAL.** The first fix for an
  invisible kind refusal re-rendered the editor -- a live input and a Save button guaranteed to be
  refused again -- twelve lines below the same module's rule forbidding exactly that. When a refusal
  is reached by an ordinary click, the question is why the click was offered (R-ET).
- **ONE SURFACE GETTING A RULE RIGHT HIDES THAT THE SHARED PARTIAL NEVER GOT IT.** The cockpit had
  rendered loan balances read-only for a year; the partial the other four surfaces include had not,
  so the rule looked shipped and was one-fifth shipped.
- **NAME A WRAPPER'S FIELD SOMETHING THE WRAPPED PRIMITIVE CANNOT ANSWER.** `ObservationDay.day`
  compiled against a raw `date` and returned the day of the MONTH -- an integer, silently, into an
  SQL bound. A value type only fences what its accessor cannot be satisfied by accident (X-f1e2).
- **A MECHANISM THAT HAS ONLY EVER BEEN RE-KEYED HAS NEVER BEEN DESIGNED.** Three migrations moved
  this index's columns to follow the schema, and each one read as a decision. Ask when the rule was
  last decided, not when it was last edited.
- **A shared primitive reached through a private import is telling you the package boundary is
  wrong.**
- **A fail-CLOSED gate is scoped by module identity, so creating a module is how you escape it.**
- **A static guard that greps for a NAME cannot tell code from prose.**
- **A GATE MUST BE EXERCISED AGAINST THE ARTIFACT IT GRADES, never read or tested only
  synthetically.** A pattern that matches the real file nowhere passes every synthetic control.
- **Scan with an AST, not a regex -- and an AST census is a grep with better manners unless it
  FOLLOWS THE DATA.** A census and a gate can be blind the same way, and then they confirm each
  other.
- **A CENSUS THAT IS NOT COMMITTED IS AN UNCITED CLAIM.**
- **COUNT THE CALL GRAPH, NOT THE CALL SITES.** One finding said four spellings; the tree held 18.
- **A COUNT IN A DOCSTRING IS A CLAIM, AND THIS ARC KEEPS WRITING IT WRONG.**
- **Every guard gets a negative control shown to fire, and a REPAIR for a dead control is itself a
  control needing the same mutation.** A correction can carry the defect it corrects.
- **A test whose fixture has no data cannot distinguish two producers.**
- **A NEW FIXTURE IS A NEW CONTROL, AND IT CAN BE BORN DEAD.** SQLAlchemy accepts an assignment to a
  field that does not exist.
- **`hasattr` on a dataclass is not a test**, and neither is `is not None` after `isinstance`.
- **A list returned for its COUNT must have its count asserted.**
- **Ask what a test's failure would have COST before deleting it, and write the answer down.**
- **CONVERTING A SURFACE TO "RAISE" BLINDS EVERY TEST WHOSE FIXTURE CANNOT REACH IT.**
- **A SUITE THAT PASSES ON 353 DAYS A YEAR IS NOT A GATE**, and the day it fails it will look like
  your change.
- **AN INSTRUMENT MUST BE SHOWN TO HAVE REACHED ITS SUBJECT.** One that cannot authenticate reports
  no differences, loudly and wrongly; one that silently grades a single subject five times reports
  five results. Assert the identity a result is attributed to, not just that a result came back.
- **A BASELINE IS ONLY A BASELINE AGAINST THE DATABASE IT WAS TAKEN FROM**, and widening an
  instrument is a shape change needing the same normalization the code does.
- **An ORACLE that states a different rule than the engine lets both be wrong together.**
- **When a conversion is mechanical, the DIRECTION of the type change has a mirror, and the mirror
  is where the bug is.**
- **A refusal is only as good as the repair it names, and nobody had pressed the button.**
- **THE STATE A GUARD DEFENDS AND THE STATE THE APP IS IN CAN BE OPPOSITES.**
- **A guard written against the wrong failure mode can still be a good guard** -- write the reason
  beside it.
- **A skip is safer to state than a fire**, when the operation being guarded is the one under test.
- **A RULING ID IS A CITATION, SO THE RULING SHIPS FIRST.**
- **REVIEW A FROZEN TREE.** Applying one review's fixes while another is running invalidates both,
  and a mutation-planting reviewer needs its own worktree.
- **A STEP WITH MULTIPLE LEAVES, MIGRATIONS AND REVIEWS SPANS SESSIONS.** Stop at the first leaf
  boundary and hand off; the alternative is a mechanical cleanup pass applied with too little care
  at the end of a long session (X-f1c3, 2026-08-04).
- **A TABLE THAT NAMES ITS OWN `HEAD` IS FALSE THE MOMENT IT LANDS**, because the commit that writes
  it moves `HEAD` past the hash it just wrote. Record the last CODE commit and the remote, which are
  stable, and tell the reader to re-measure the rest.
- **AN IMPOSSIBILITY ARGUMENT THAT CONSIDERS ONE LOCK CLASS IS NOT AN IMPOSSIBILITY ARGUMENT.**
  X-f1c3c's first docstrings called deadlock "structurally impossible on every request path" having
  reasoned only about advisory-vs-advisory ordering; the real cycle was advisory-vs-ROW, and it
  REPRODUCED against a real PostgreSQL (**N-193**). Name the classes an impossibility claim ranges
  over, or it is a claim about the argument rather than about the system.
- **A NARROWING MUST ASSERT WHAT IT LEAVES ALONE, NOT ONLY WHAT IT REMOVES.** X-ap narrowed the
  status dropdown's offer set by subtracting the settled BAND, which has three members, so the
  ARCHIVE transition silently stopped being offered on every row -- retiring the only control that
  reaches it. Ten controls shipped with that step and none could fail on it, because every one used
  a Projected row and the loss lived in the settled band. Two docstrings written in the SAME PR
  asserted the opposite ("the full-edit Status dropdown offers Settled from Paid"), which is the
  tell: **when a change makes a claim elsewhere in its own diff false, the claim was the test that
  was missing.**
- **READING A LAZY RELATIONSHIP IS A FLUSH**, so where a guard SITS decides which exception net
  catches the request's first UPDATE. X-ap moved a refusal that asks `tracks_purchases` above the
  handler's `try`; that read lazy-loads `template`, autoflushing the staged `setattr` mutations from
  outside the net -- turning a designed 409 into a 500, and a period move into an uncaught
  `IntegrityError` because the flag that lifts the row out of a partial unique index had not been
  written yet. **A guard placed for READABILITY moved a database write.** Ask of every pre-mutation
  check whether it touches a relationship, and put it where the flush belongs.
- **A READ DOOR THAT GAINS A RAISE BREAKS EVERY REPAIR PATH THE UI ADVERTISES THROUGH IT.** R7b-1
  made `recurrence_spec` decode, so it refused a rule whose stored pattern the application no longer
  models -- on the way to REPLACING that pattern, which is the one action the edit form tells the
  user to take ("Pick a new pattern before saving"). The suite had a control for saving such a rule
  UNCHANGED and none for repairing it, so the advertised remedy became a 500 with every gate green.
  The fix was structural -- read everything EXCEPT the fact being replaced -- and the shape
  generalises: **when a reader starts validating, ask which of its callers were about to overwrite
  the thing it now validates.**
- **Documents rot in days here.** This file is the only one allowed to rot, and every edit re-dates
  it.
