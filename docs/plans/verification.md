# The verification standard

**One copy of what "done" means for a step in any arc.** It lived in the balance README's section 7
and, in a shorter and not-quite-equal form, in the credit-card plan; the recurrence and pay-calendar
arcs had none at all, which is how the two arcs at the front of the queue came to be held to nothing
written down. That is the same denormalization `conventions.md` and `lessons.md` exist to remove.

**This is the ORACLE discipline, not the release checklist.** What every commit owes -- full suite,
`pylint app/` with the full `--fail-on` set, migrations tested in both directions, the test template
rebuilt after one -- is `CLAUDE.md`'s Definition of Done, and restating it here would be a second
copy beside no reconciler. This document answers the question the Definition of Done does not: how
do you know the number is RIGHT.

## The standard

1. **The baseline must not move** unless the step's design says it moves, in which case every moved
   number is individually explained and signed off.

2. **Oracles are exhaustive and independent.** Every day, every shape; never a sample; never two
   producers that share code proving each other. **Never a producer as its own oracle.** The fold is
   the reference.

3. **Ask of every harness: can it SEE the code under test?** Three exist because each is blind where
   the next one looks, and a harness blind to a step reports byte-identical -- a free pass that
   reads as proof.

   | harness | what it can see |
   |---|---|
   | `tests/manual/verify_balance_baseline.py` | Every figure the seam can answer about every account in a database. Run before and after, `diff` the blobs. DETERMINISTIC, and a REGRESSION check rather than a proof: two identical figures can both be wrong. Every figure is read at the seam's default `as_of`, so a step scoped to a pinned historical `as_of` moves nothing in it |
   | `tests/manual/verify_savings_producers.py` | Above the seam, where the first is blind: a producer package, a serializer or a template |
   | `tests/manual/verify_anchor_surfaces.py` | The anchor surfaces both others miss: the grid header's figure and "as of" caption, the reconcile panel, the dashboard balance section, the pulse hero, the savings dashboard including the ARCHIVED drawer, Property market value / home equity, and the retirement seeds. A producer that raises is RECORDED rather than fatal -- a probe that dies on account 3 has silently stopped covering 4 through 9 |

   **Use `git worktree` for the HEAD side, never `git checkout`.**

4. **Every guard gets a negative control that is SHOWN to fire.** A guard whose control does not
   fire is not a guard.

5. **The fixture matrix must contain the shape the feature exists for** -- a paid loan, an
   off-schedule payment, a delinquent loan, a card past its grace.

6. **The suite has two clock gates.** CI runs `TZ: Pacific/Kiritimati`, so a `date.today()` /
   `display_today()` mix fails there, and a weekly sweep runs the suite at a leap day, both sides of
   a year boundary, a month end and the first of a month. **Read `docs/test-suite-clocks.md` before
   writing a fixture that touches an anchor, an assertion instant or a due date.**

7. **Green gates are necessary, never sufficient.** A `$197,049.32` defect passed pylint 10.00 and a
   7,387-test suite. Live-render the affected surfaces against the dev clone before landing anything
   that moves a figure a screen shows.

8. **No uncited claims in a planning document.** Anything stated as fact about the code carries its
   own commit hash or was verified on its write date; when you edit a file, re-verify what you
   touch.

## What is arc-specific and stays in the arc

The standard above is what every arc owes. A step whose acceptance needs a WALK through the app --
the credit-card arc's end-to-end run from creating a card to the next payment shrinking, for
instance -- states that walk in its own document, because it is a specification of one step's proof
rather than a rule about proofs. The distinction is `conventions.md`'s:
**merge what shares KEYS, split what shares only a READER.**
