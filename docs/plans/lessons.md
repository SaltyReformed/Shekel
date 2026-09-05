# Process lessons

**One copy of what this project has already paid to learn, shared by every arc.**

**One line each, and the line is the LANDMINE, not the story.** A lesson earns its place by having
COST something -- a rebuild, a wrong figure, a review round -- and it names the step or finding that
paid. The narrative stays in that commit: this file only works if it is read straight through, and
prose is what stops that happening. An append-only file nobody finishes reading loses its lessons as
completely as deleting them would.

**A lesson MECHANIZED into a gate is RETIRED from here**, its line moving to that gate's own
rationale, where a reader meets it at the moment it fires.

**Never record STATE here** -- what shipped, what is next, what production runs. That is
`steps.md`'s, and a sentence about it here would be a second answer beside no reconciler.

## The rule itself

- **An argument a caller can get wrong is a defect, not a contract.**
- **A DRY refactor of a PREDICATE can move money.** Two spellings that agree by reading are two
  answers until one is deleted.
- **When two figures PARTITION a set, write both halves from ONE predicate**, or the boundary drifts
  and both halves look right.
- **CENTRALISING one clock WIDENS the gap to every clock it did not centralise.** `C2-f2d-1` closed
  a `$4.18` split and opened a `$2,173.38` one, an `as_of=None` default a whole producer away.
- **When a rule is re-keyed, the complement must move with it**, or replay and projection stop being
  exact complements.
- **When two sides of one problem have different SHAPES, the loose side is where the next hole is.**
- **A RULING THAT FALSIFIES A PREMISE MUST BE GREPPED FOR THE PREMISE, NOT THE SYMPTOM.** X-f3b let
  a Projected row hold postings; six write paths were safe only because it could not, and review
  found four. Search for the SENTENCE the old code relied on.
- **A BOUND JUSTIFIED BY "THIS DIRECTION IS CONSERVATIVE" INVERTS WHEN WHAT IT BOUNDS CHANGES
  MEANING.** `TransactionEntry.settled_on` was unbounded above because a forward day held the budget
  back; the same day RELEASES it once a posting books cash (X-f3b / R-FM). Re-read every bound whose
  reason is a direction, not a fact.
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
- **A RULE THAT READS A CLOCK MUST BE APPLIED ONCE**, and "the writer distrusts its caller" is not a
  reason to apply it twice; carry "already checked" in a TYPE only the checker can mint (X-f1e2, a
  midnight roll between two applications of one floor).
- **AN APPEND-ONLY TABLE NEVER LICENSES AN UNSERIALISED READ-MODIFY-WRITE IN THE SAME TRANSACTION.**
  Name the tables the transaction WRITES, not the one the ruling is about (R-EN, N-190).
- **IDEMPOTENCY IS A PROPERTY OF A REQUEST, NOT OF THE ROW IT CARRIES.** A unique index over the
  row's values must mis-classify a retry or a re-assertion; ask which way, and what it costs (R-EQ).
- **WHEN TWO DOORS WRITE ONE FACT, ALIGNING THEM CAN BE WORSE THAN DELETING ONE.** Ask what each
  door's SURFACE means before making them agree (X-f1e1).
- **AN IMPOSSIBILITY ARGUMENT THAT CONSIDERS ONE LOCK CLASS IS NOT AN IMPOSSIBILITY ARGUMENT.** Name
  the classes it ranges over: the deadlock ruled out advisory-vs-advisory reproduced advisory-vs-ROW
  against a real PostgreSQL (N-193).
- **READING A LAZY RELATIONSHIP IS A FLUSH**, so where a guard SITS decides which exception net
  catches the request's first UPDATE -- a guard moved for READABILITY moved a database write (X-ap).
- **A READ DOOR THAT GAINS A RAISE BREAKS EVERY REPAIR PATH THE UI ADVERTISES THROUGH IT.** When a
  reader starts validating, ask which callers were about to overwrite what it now validates (R7b-1,
  where the advertised remedy became a 500 with every gate green).
  **A refusing read and a TOTAL one are two questions**, so publish the total beside it rather than
  softening it, which would delete the refusal for every other caller (X-au-c3).
- **NAME A WRAPPER'S FIELD SOMETHING THE WRAPPED PRIMITIVE CANNOT ANSWER.** `ObservationDay.day`
  compiled against a raw `date` and returned the day of the MONTH into an SQL bound (X-f1e2).
- **A shared primitive reached through a private import means the package boundary is wrong.**
- **A fail-CLOSED gate is scoped by module identity, so creating a module is how you escape it** --
  and SPLITTING one is creating several, because the allowlist matches a package PREFIX. Name the
  ONE leaf that still needs the exemption (`transfer_service` paid; `status_seam` nearly did).
- **TWO INDEPENDENT FACTS IN ONE REQUEST ARE NOT ALTERNATIVES**, and a door that decides which the
  caller meant drops the other (X-au-c3: a corrected Actual archived nothing, and answered 200).
- **A CONSTRAINT CANNOT HOLD A CONVERSATION.** The door owes the CHECK's rule in words with the
  repair in the message, or the user meets it as "invalid reference" (X-au-c3).
- **FIVE RULES FOR ONE EXTRAPOLATION, EACH MEASURED WRONG (`salary:R14-b`, 2026-09-04).** Past the
  saved calendar a modelled payroll deduction has to come from somewhere, and every rule that
  GUESSES it is exact on the shape its author had in mind. On a window holding no whole year: the
  last payday that PAID something reads a capped deduction's clamped final payment as its rate
  (**10.4x**); a year average over the paydays OBSERVED reads **2.00x** the cap at 13 paydays and
  **15.6x** at 1; refusing outright never overstates but flattens the committed line while the
  what-if overlay beside it stays at the user's figure, so a SMALLER what-if reads as LARGER. One
  cause under all three: a sub-year window has thrown the cap away, since a `$500`-a-payday
  deduction and a `$1,000`-capped one price IDENTICALLY for two paydays. Two more failed on windows
  that DO hold a year -- dividing it by the CADENCE (**+3.846%** on the 9.07% of default windows
  whose year holds 27 biweekly paydays) and grading completeness by a COUNT (a 27-payday year passes
  at 26 observed; **60%** understated). **The remedy was not a sixth rule.** It is `S3`: the engine
  prices every payday of the horizon, and nothing extrapolates.

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
- **ONE SURFACE GETTING A RULE RIGHT HIDES THAT THE SHARED PARTIAL NEVER GOT IT.** Loan balances
  were read-only on the cockpit for a year; the partial four other surfaces include was not.
- **A JS visibility defect is invisible to pytest** -- drive it in a browser (X-f2-b: 8,556 green
  over a toast that reached the DOM and never appeared).

## Tests and controls that cannot fail

- **Every guard gets a negative control shown to fire, and a REPAIR for a dead control is itself a
  control needing the same mutation.** A correction carries the defect it corrects, and
  **a CARVE-OUT needs a mutation per DIRECTION**: absent and too-permissive are ONE experiment.
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
- **WHEN A CHANGE MAKES THE CORRECT FIGURE EQUAL THE OLD DEFECT'S FIGURE, THE FIXTURE MUST MOVE.**
  X-f3b made a part-spent envelope `posted + reserved`, which for an under-budget row is exactly the
  estimate F-002 printed, so the `$500` assertion stopped telling them apart (fixture: OVERSPEND).
- **An assertion that cannot fail is not a test**: `hasattr` on a dataclass, `is not None` after
  `isinstance`, and a list returned for its COUNT whose count is never asserted.
- **CONVERTING A SURFACE TO "RAISE" BLINDS EVERY TEST WHOSE FIXTURE CANNOT REACH IT.**
- **A SUITE THAT PASSES ON 353 DAYS A YEAR IS NOT A GATE**, and the day it fails it will look like
  your change.
- **Ask what a test's failure would have COST before deleting it, and write the answer down.**
- **THE STATE A GUARD DEFENDS AND THE STATE THE APP IS IN CAN BE OPPOSITES**, and one written
  against the wrong failure mode can still be a good guard -- write the reason beside it.
- **A skip is safer to state than a fire**, when the operation being guarded is the one under test.
- **A GATE OVER A MAPPING NEEDS BOTH ARMS, OR IT CATCHES A RENAME AND MISSES AN ADDITION**, and its
  docstring will claim otherwise -- read what a gate asserts, not what it says it asserts (R7c-c).
- **AN ASSERTION ABOUT PERSISTED STATE MUST NAME THE STORE THE CODE UNDER TEST WRITES TO.** A
  hard-coded database name beside an app pointed at a copy passes with every refusal ACCEPTED.

## Instruments, oracles and censuses

- **AN INSTRUMENT MUST BE SHOWN TO HAVE REACHED ITS SUBJECT.** One that cannot authenticate reports
  no differences, loudly and wrongly. Assert the identity a result is attributed to, not just that a
  result came back.
- **A HARNESS THAT ADAPTS TO BOTH SIDES CAN GRADE NEITHER'S CONFIGURATION.** `C2-f2d-1`'s ran a pass
  per producer on both, so 37,295 identical lines said nothing about the SHARING that shipped.
- **A GATE MUST BE EXERCISED AGAINST THE ARTIFACT IT GRADES**, never only synthetically: row
  **P52**'s `grep` canary is line-based against a call site that WRAPS, so "the gate passes" has
  always meant "nothing matched".
- **AN ORACLE IS WORTH ONLY ITS INDEPENDENCE FROM WHAT IT GRADES**, and there are three ways to lose
  it: STATING a different rule than the engine, which lets both be wrong together; AGREEING with a
  producer that READS the value being replaced (X-au-b scored 997 of 997 by
  `return txn.estimated_amount`); and MINING the expected series out of the column under test
  (X-au-a backfilled the price history FROM the rows X-au-b's 452 agreements then re-attested).
  **INVARIANCE is the control that sees all three** -- perturb the input the new rule must not be
  reading and require the answer not to move. Ask where the expected value came FROM, not just
  whether it differs from the code under test.
- **A BASELINE IS ONLY A BASELINE AGAINST THE DATABASE IT WAS TAKEN FROM**, and widening an
  instrument is a shape change needing the same normalization the code does.
- **A CENSUS'S METHOD BOUNDS WHAT IT CAN FIND.** Scan with an AST, not a regex; an AST census is a
  grep with better manners unless it FOLLOWS THE DATA; one that greps for a NAME cannot tell code
  from prose; and COUNT THE CALL GRAPH, NOT THE CALL SITES -- one finding said four spellings, the
  tree held 18. A census and a gate can be blind the same way, and then they confirm each other.
- **A CENSUS THAT IS NOT COMMITTED IS AN UNCITED CLAIM.**
- **A COUNT IN A DOCSTRING IS A CLAIM, AND THIS ARC KEEPS WRITING IT WRONG.**
- **BEFORE READING A CLEAN DIFF, CHECK THE FIGURE THE STEP IS ABOUT IS NOT NULL IN IT.** C2-f2c's
  harness was byte-identical over 39,939 lines while `retirement_marker_index` (**P48**) was `None`
  on every one. A clone samples one owner's CHOICES: WRITE the state in and re-capture both sides.
- **PLANT THE FIRING CONTROL AT EVERY WIRING SITE, AND CHECK THE PLANTED DEFECT IS REACHABLE ON THE
  REAL DATA.** C2-f2a's first dropped the axis's FIRST period and moved `$0.00` (they precede the
  latest assertion), so the harness read blind; at the LAST it moved `-$182.29` and `-$190.39`.

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
- **A WITHDRAWN HAZARD IS THE SENTENCE NOBODY RE-CHECKS**: it closes the question a stated one keeps
  open. The withdrawer owns the correction -- say what a SHA touches, not what you do not.
- **Documents rot in days here.** This file is the only one allowed to rot, and every edit re-dates
  it.
- **IN FIVE CONSECUTIVE ADVERSARIAL PASSES, THE DEFECTIVE PART WAS A SENTENCE THE FIX WROTE ABOUT
  ITSELF** (`salary:R14-b`, 2026-09-04). Every measurement held; the self-descriptions did not. The
  fourth instance was written WHILE fixing the third, and two more surfaced only because a line
  ceiling forced a docstring to be re-read. A fix's claims about its own correctness are written
  LAST, under post-measurement confidence, and are the one thing its own tests cannot grade. **Grade
  them separately, and never let the pass that fixes them also be the pass that wrote them.**
