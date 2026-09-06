# Runbook: the account-10 repair

**STATUS: SUPERSEDED BY RULING `balance:R-BAL3` (2026-09-05). DO NOT PERFORM ANY ACT BELOW.**
Nothing here has been done to production, and nothing here may be. **Four of its six acts are
wrong under the new ruling and one of them MOVES MONEY THE WRONG WAY**: act 1 deletes transfer
102, which R-BAL3 KEEPS as the surviving record of a real `$500` ACH; act 3 opens account 10's
books on 2026-03-26 at `$5,350.21`, where R-BAL3 opens them 2026-03-25 at `$4,850.21`; act 4a
drops transfer 1 for the wrong reason; and act 4b books `$500` of `Financial: Emergency Fund`
expense the developer rejected in writing (*"I don't like faking or hiding money"*).

**BOTH of the blockers this document names have since cleared, which is exactly why the banner is
needed rather than a note.** The restatement door IS deployed -- production is `9de30bce`, which
contains `59b485df`, measured 2026-09-05 by the `docker inspect` recipe below -- and act 4b is no
longer OPEN but DELETED. A reader who checked only those two would conclude the procedure is
unblocked and work it.

**What replaces it.** `balance:R-BAL3` in `../../plans/rulings.md`: the `$500` ACH left Checking
and reached Fidelity on 2026-03-26, the day BOTH accounts' books opened, so under **R-HG** it was
absorbed on both sides and had nowhere true to live. Both accounts now open **2026-03-25** at
their banks' own closes -- `$1,234.04` for Checking, `$4,850.21` for account 10 -- and the FOUR
bank lines of 2026-03-26 are RECORDED on 2026-03-26, the `$500` among them as one ordinary
transfer on the day both banks posted it. Act 4b does not exist. The step also waits on
`pay_calendar:C18`.

**This document is kept, not deleted, because its INSTRUMENTS and its measurements are still
true**: the door census, the flash-refusal trap, the stop-rule reasoning, the archive round trip
and the "what you will and will not see move" table were all measured and all survive. The ACTS
and their figures are what changed. Read it as evidence, never as instructions.

**The rewrite is owed by `balance:X-f3c-2b-2c`** and has not been done: its rehearsal must run
after `pay_calendar:C18` ships, so rewriting the acts now would rehearse them twice.
**Five things the rewrite owes, found by the neutral review of 2026-09-05 and recorded here so
they are not lost with this document:**

1. **The "what you will and will not see move" table below is FALSE for the new acts.** Under
   R-BAL3 Checking's own daily balance moves on four days -- 2026-03-26 by `+$2,493.43`, 04-29
   and 04-30 by `+$1,500.00`, 07-23 by `+$2,000.00` -- and where the bank can grade them they are
   IMPROVEMENTS: SECU states `$3,409.57` for 04/29 and the app then matches it to the cent. An
   operator reading today's table would call the `+$2,000.00` jump an error.
2. **A stop rule is missing for a window the new order opens.** Between the restatements and the
   dividends, account 10's income statement carries **`+$29.05` of interest income that never
   happened** -- ruling **R-FO** sends an interest-bearing account's true-up counter leg to
   `interest_income`. It is the harm R-HL's order exists to prevent, with the opposite sign.
3. **Transfer 102's re-date needs BOTH openings already at 2026-03-25**, not just its own
   account's: `settle_day.record_settle_day` asks the books boundary per row, and that one edit
   touches Checking and account 10. A per-account "restate, then re-date" reads as safe and is
   one click from a refusal mid-act.
4. **The `-$108.87` line's CATEGORY is unruled.** The rehearsal booked it to `Family:
   Subscriptions` (category 17, Audible's) as a placeholder; SECU files it `Shopping/Online` and
   the owner has no shopping category. The developer names it, not the runbook.
5. **The opening-day corroboration arm cannot fail in a REHEARSAL** -- both sides come from the
   same export -- so it grades the human's typing and nothing else. Today's document says this;
   the rewrite must keep saying it.

---

## What the rewrite must encode: the acts, in the order they were driven

**This is the ordered sequence a rehearsal of `R-BAL3` actually performed and verified on a clone
of production, 2026-09-05 -- 36 door acts.** It is recorded here because the ordered list further
down is the SUPERSEDED one, and because the session that proved this sequence held it in a
scratchpad that does not survive. Re-rehearse it; do not trust it.

**The order is FORCED at two points, not chosen.** `budget.books_hold` is a strict
`p_day > p_opened_on`, so no row can be re-dated onto 2026-03-26 until the books it belongs to
already open 2026-03-25 -- which is why both restatements precede every re-date. And transfer 102's
re-date touches BOTH endpoints, so BOTH Checking's and account 10's openings must be at 2026-03-25
before that single edit; `settle_day.record_settle_day` asks the boundary per row, so a per-account
"restate, then re-date" walk is one click from a refusal mid-act.

1. **Delete transfer 1** (Checking -> the archived twin, `$500.00`). Template-linked, so a SOFT
   delete -- **N-386**, whose exposure is now `$500.00` rather than `$1,000.00` because transfer
   102 survives.
2. **Restate account 10's books** to **2026-03-25** at **`$4,850.21`** -- Fidelity's carried-forward
   close, its 03/12 line, nothing moving until 03/26.
3. **Restate Checking's books** to **2026-03-25** at **`$1,234.04`** -- SECU's own stated close for
   that day (`~/Downloads/checking/2026_ytd_daily_balances.csv`).
4. **Re-date transfer 102 onto 2026-03-26.** It already points Checking -> account 10 and already
   carries `occurs_on` 2026-03-26; only its settle day (2026-04-06) is wrong.
5. **Re-date the three Checking rows the bank posted on 2026-03-26** from 2026-03-27:
   **781** (`$100.00` Health Insurance Allowance), **865** (`$2,473.38` Data Manager) and
   **1069** (`$15.96` Audible). The first two answer ONE bank line and are `$0.04` short of it,
   which is **BAL-467** and stays open.
6. **Record the bank line the app holds nowhere**: `$108.87`, 2026-03-26, category
   **`Family: Birthday` / `Josh's Birthday`** (developer, 2026-09-06) -- **BAL-468**.
7. **Re-date the five transfers onto the bank's own days** (156, 154, 157, 346, 409; 155 already
   sits on its day and must be skipped by MEASUREMENT, not by position).
8. **Consolidate the twin in ONE sitting**: unarchive, restate its opening to `$0.00`, assert
   `$0.00` observed 2026-04-06, re-archive. Do not stop while it is unarchived.
9. **Record the five dividends** (**R-HL**), after the restatements and not before.
10. **Assert `$3,673.90` observed 2026-07-31** (**R-HM**), in the same sitting as act 9.

**What it produced, and what the rewrite's own rehearsal must reproduce**: Checking's books read
`$1,234.04` on 2026-03-25 (bank exact) and `$3,182.59` on 2026-03-26 against the bank's `$3,182.63`
-- the `$0.04` of **BAL-467** and nothing else; account 10 reads `$4,850.21` and `$5,350.21`, both
bank exact. Class moves: Asset `-$5,349.17`, Equity `+$5,283.74`, Expense `+$108.87`, Income
`-$43.44`, Liability and Unrealized `$0.00`. Account 10 scores **15 of 15** exact on the cash-fold
and cutover arms and 14 of 15 on the rendered arm; Checking's opening corroboration reads `$0.00`.

**Rehearse against the EXTENDED calendar.** Every figure above was measured with the pay calendar
still opening 2026-03-26, because `pay_calendar:C18` had not shipped. C18 puts a period at
2026-03-12 and bounds generation by the books, so the rehearsal has to be re-run on that tree
before anybody clicks -- these numbers are the target, not the evidence.

---

## THE SUPERSEDED PROCEDURE FOLLOWS. It is a record of what was rehearsed on 2026-09-01, and it
## is NOT a set of instructions.

---

## What this is

Account 10 (*Fidelity Money Market Savings*) and archived account 2 (*Fidelity Savings*) are the
same real Fidelity account. The app's record of it disagrees with Fidelity's own export in five
ways -- findings **N-379**, **N-382** and **N-384** in `../../plans/ledger.md`: the books open on the
wrong day at the wrong figure, one real ACH is recorded as two transfers, five transfers sit on days
the bank did not post them, five dividends were never recorded at all, and the archived twin still
carries the whole balance on the balance sheet.

**The repair is performed by an owner clicking through the app**, and that is ruling **R-HJ**: a
migration writing those money rows would be a second writer beside every door that already performs
these acts, and would hard-code one owner's account ids and dates into every future deploy. The
figures, the acts and their order are ruled in `../../plans/rulings.md` at **R-HJ** through
**R-HM**; this document is how you carry them out, not a second statement of what they are.

**Every bank figure below is derived, not copied.** The opening equity is the export's own closing
for the day the books open, the dividends are its `DIVIDEND RECEIVED` lines, and the final assertion
is its last stated close. `tests/manual/rehearse_account_10_repair.py` re-derives all of them at run
time and refuses to start if the stated transfer map no longer reconciles -- in both directions:
against the export AND against the transfer rows themselves.

The export is `~/Downloads/History_for_Account_Z29868989.csv`.

---

## Before you start

**1. Is the door deployed?** Ask the running container what it is, and git whether that revision
carries the door:

```bash
REV=$(docker inspect shekel-prod-app \
        --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
git merge-base --is-ancestor 59b485df "$REV" && echo DEPLOYED || echo NOT DEPLOYED
```

Measured 2026-09-01: production is `efbffbd5` and the answer is **NOT DEPLOYED**. If it still is,
stop -- a release has to ship first. The same question by clicking: open any account's **Edit** page;
if it renders a **Books opening** card, the door is there.

**2. Take a backup, and verify it.** `scripts/backup.sh`, then `scripts/verify_backup.sh`. This
procedure has no undo: an opening restatement and a balance assertion are both append-only, so a
wrong figure is corrected by stating another one, never by removing a row.

**And write these two figures down before you touch anything, because nothing else records them.**
An opening is restated by stating a NEW one, so going back means typing the old figure -- and after
act 3 or act 4c the old figure is no longer on any screen. Measured on a production clone
2026-09-01, and re-read them yourself rather than trusting this table, since a restatement between
now and the repair would move them:

| account | pre-repair opening day | pre-repair opening equity | source |
|---|---|---|---|
| 10 *Fidelity Money Market Savings* | 2026-04-05 | `$4,879.26` | `migration_derived` |
| 2 *Fidelity Savings* (the twin) | 2026-03-26 | `$4,863.56` | `migration_derived` |

**Act 6 is the one act that is not undoable by restating.** Its figure can be corrected by asserting
again, but its EFFECT cannot: ruling **R-HM** works by moving the modelled-accrual window to the
latest assertion, and a later assertion cannot move that window back. "Corrected by stating another
one" is true of the figure and false of the window.

**3. Rehearse on a clone, and keep the BEFORE.** Clone production into a throwaway database, take it
to head, then:

```bash
DATABASE_URL=postgresql://.../<clone> \
    .venv/bin/python tests/manual/measure_cutover_against_bank.py \
    --account 10 --format fidelity \
    --bank ~/Downloads/History_for_Account_Z29868989.csv

DATABASE_URL=postgresql://.../<clone> \
    .venv/bin/python tests/manual/rehearse_account_10_repair.py \
    --clone <clone> \
    --bank ~/Downloads/History_for_Account_Z29868989.csv
```

The rehearsal performs everything below through the same doors you are about to click and verifies
the post-state. **It refuses a clone the repair has already run against**, so re-running it means
restoring the clone first. If it fails, this document is wrong and the repair does not start.

---

## The acts, in order

**The order is ruling R-HL's, and one step of it is enforced -- but the enforcement catches you
LATE, so do not lean on it.** Recording the 2026-03-31 dividend before act 3 takes three saves, and
the first two are accepted: the row is created, and marking it Received succeeds because that stamps
**today's** date, not the day you mean. Only the third save -- correcting the day to 2026-03-31 --
meets the books boundary, and the app answers *"Money cannot have moved on 2026-03-31: this
account's books open on 2026-04-05 holding $4,879.26..."*.

**What that leaves behind is not an inert draft.** Measured: a settled `Received` row for `$13.35`
on account 10, dated the day you are working, live in every balance. Work the acts in order; if you
do hit that refusal, delete the settled row before continuing -- deleting it reverses posted journal
entries, so it is a real act rather than discarding a draft.

**You may stop between acts -- except between 5 and 6, and NOT anywhere inside act 4.** Every other
boundary leaves the books internally consistent. Act 4 has two windows of its own, and the first is
worse than the 5-to-6 one:

* **Between 4a and 4b** the `$500.00` that genuinely left Checking on 2026-03-27 is recorded
  NOWHERE. Bounded, and it self-corrects when you come back, but Checking's displayed balance does
  not move while it lasts (its own later assertion resets the fold), so there is no cue.
* **Between the UNARCHIVE and 4d the twin's whole balance is counted TWICE, and this is the worst
  state this procedure can be left in.** Act 3 has by then put that money inside account 10's
  opening; 4c takes the twin's opening to `$0.00` but its 2026-04-06 assertion still says
  `$5,363.56`, and the restatement door's own docstring measured that exact case
  (`routes/accounts/opening.py`): taking a `$4,863.56` opening to `$0.00` "leaves the 2026-04-06
  assertion booking a `$5,363.56` true-up and the asset returns in full." Archived, only the balance
  sheet saw it (**N-384**); UNARCHIVED, every dashboard does too. So the twin must not be left
  unarchived: do 4c, 4d and the re-archive in one sitting.

Between 5 and 6 the latest assertion is still 2026-07-16, so the dividend you
have just recorded on 2026-07-31 sits **inside** the open modelled-accrual window and is counted
twice: account 10 reads `$3,680.32` against the bank's `$3,673.90`, a **`$6.42`** overstatement, and
that is the exact shape ruling **R-HM** exists to prevent. Do 5 and 6 in one sitting.

### Act 1 -- delete the duplicate ACH

Open **transfer 102** (*Checking -> Fidelity Money Market Savings Contribution*, `$500.00`, recorded
as settling 2026-04-06) and delete it.

It is **N-382**: one real `$500` ACH recorded twice, once into the twin and once here, so Checking
was debited twice for money that left it once. Fidelity's export has no 2026-04-06 line at all.

It is template-linked, so the app can only SOFT-delete it -- the row stays restorable through the
recurrence conflict chooser. That limitation is accepted and is finding **N-386**; the money leaves
correctly either way, because the fold excludes deleted rows and the posted effect is reversed
before the row goes.

*What you should see:* the row disappears from the grid. **No displayed balance changes** -- see
"What you will and will not see move".

### Act 2 -- re-date five transfers onto the bank's own days

For each, open the transfer's full-edit card from its account-10 grid cell and change only
**Money moved on**. Leave the amount, the status and everything else exactly as rendered.

| transfer | what it is | recorded | the bank posted it |
|---|---|---|---|
| 156 | Checking -> account 10, `$500.00` | 2026-04-11 | **2026-04-23** |
| 154 | account 10 -> Checking, `$1,500.00` | 2026-04-23 | **2026-04-29** |
| 157 | Checking -> account 10, `$500.00` | 2026-05-10 | **2026-05-07** |
| 346 | Checking -> account 10, `$250.00` | 2026-05-16 | **2026-05-14** |
| 409 | account 10 -> Checking, `$2,000.00` | 2026-07-24 | **2026-07-23** |

Transfer **155** (`$500.00`, 2026-04-09) already sits on the bank's day. Do not touch it. It is
listed because the six together account for every movement the export shows after the books open -- a
list naming only the five would be a set defined by subtraction.

*What you should see:* each card saves without complaint. A settle-day correction is legal on a
finalised transfer by design: the lock protects budget decisions, and the day the bank moved money
is an observed fact. The card can also be opened from the **Checking** side -- the rehearsal drives
the account-10 side, so if you use the Checking cell you are on a path nothing here has exercised.

**A re-date moves the day and NOT the pay period**, so a transfer can end up filed in a period that
does not contain its own settle day. Transfer 346 is filed in the period starting 2026-05-21 and
moves to 2026-05-14, which is outside it. **The re-date does not CAUSE that** (measured on a
production clone 2026-09-01): its recorded 2026-05-16 was already outside the same period, whose
neighbour runs 2026-05-07 to 2026-05-20. So the row arrives mis-filed and leaves mis-filed, one day
further out. That is ordinary in this app -- a settle legitimately falls outside its period, and
11 of 156 settled rows on an earlier production clone already did -- but it is worth knowing before
you go looking for the row on the grid.

### Act 3 -- restate account 10's books

Account 10 -> **Edit** -> the **Books opening** card. Day **2026-03-26**, equity **`$5,350.21`** -- the
export's own closing for that day. Save.

Ruling **R-HK**: 2026-03-26 is the first day the owner's pay calendar covers, and every bank line on
or before it is absorbed into the equity rather than recorded -- six lines running back to
2026-01-30, which the app has no pay period to hold.

*What you should see:* a flash saying the books were restated, and a warning that balances recorded
afterwards are unchanged so the difference shows as a correction against them. That is expected, and
act 5 is what clears it. **If you see a red flash, the day or the figure was refused and nothing was
written** -- the door reports a refusal by flashing and returning you to the same page, so read it.

### Act 4 -- consolidate the archived twin onto account 10

**4a.** Open **transfer 1** (*Emergency Fund*, Checking -> Fidelity Savings, `$500.00`, 2026-03-27)
and delete it. Also template-linked, so also a soft delete -- **N-386** again, and this second
instance doubles that finding's standing exposure from `$500.00` to `$1,000.00`.

**4b IS OPEN AND MUST NOT BE PERFORMED AS WRITTEN. STOP HERE UNTIL IT IS RULED.**

The developer rejected this act's method on 2026-09-01, on the principle *"I prefer root cause
solutions. I prefer the from scratch design. I don't like faking or hiding money."* Booking an
expense that did not happen is what he is refusing, and the two alternatives so far offered do not
survive the same principle either: recording NOTHING hides the same real outflow inside an equity
correction against Checking's next assertion, and opening the books a day earlier was rejected on
its own grounds by **R-HK**. **The correct double-entry design is owed and has not been written.**
What is true and not in dispute: the `$500.00` genuinely left Checking on 2026-03-27, it is not
spending, and account 10's restated opening already holds it -- so the movement is Checking into
another account's OPENING EQUITY, which is a shape this app has no way to record.

Acts 1, 2, 3, 4a, 4c, 4d, 5 and 6 are unaffected and remain as ruled. **The figures below are what
was rehearsed, kept so the rehearsal stays reproducible -- they are NOT an instruction.**

*The rehearsed method was:* on the grid, in Checking's **Financial: Emergency Fund** row for the pay
period starting 2026-03-26, create an expense of **`$500.00`**; mark it Paid; then reopen the
now-settled card and set **Money moved on** to **2026-03-27**.

**Three saves, and finish all three before moving on.** The day box only appears once a row is
settled, so the day cannot be stated at creation -- and marking it Paid stamps **today**. Between the
second and third save the `$500.00` is live in your balances on today's date. It is transient, but
it is real while it lasts, so do not leave a row half-recorded.

**Two things about this act are not what they look like.** *It is not on the bank's day*: SECU
posted the ACH on 2026-03-26, which is the day Checking's own books open, and ruling **R-HG**
refuses a movement on or before `opened_on` -- so the app's own 03-27 stands. It is the one date this
repair does not move onto the bank's. *And it books `$500.00` of EXPENSE that did not exist before*:
the deleted transfer's Checking leg posted as a `transfer` and touched no expense row, so total
expenses rise `$25,773.39 -> $26,273.39` and a `Financial: Emergency Fund` line appears in your first
pay period. The money did not leave your net worth -- it is inside account 10's restated opening -- so
the income statement now reports spending that did not happen. Ruling **R-HK** names this act but
not that consequence.

**4c and 4d still need the twin UNARCHIVED first, and 4c now needs it for a DIFFERENT REASON.**
This paragraph used to give one reason for both: while an account is archived the cockpit offers
only *Unarchive* and *Delete*, so neither door had a click path. That was finding **N-430**, and
plan step `balance:X-f3c-2b-2d` closed it -- the archived card carries the same **Edit** link the
live cell's kebab does, so 4c's own instruction below (*Account 2 -> **Edit** -> **Books opening***)
is clickable while the twin is still archived. **4d is not reachable BY CLICKING, which is not
the same as unreachable**, and the set is stated by CENSUS rather than by subtraction. Five
templates open the shared anchor editor: `accounts/_cash_balance_hero.html`,
`investment/_balance_hero.html`, `loan/_balance_hero.html`, `dashboard/_pulse_balance.html` and
`savings/_cockpit_balance.html`. Two of the five can never render for an archived account -- the
dashboard pulse resolves its hero through `account_resolver`, whose
`_first_active_checking_account` filters `is_active=True`, and the cockpit's live cell is the branch
an archived account does NOT take, because it renders in this drawer instead. The three that remain
are detail-page heroes, and the archived card's own figure is static text, so nothing here links
them. Typed
directly, `/accounts/2/details` answers **200 with a working balance editor**, so this is a reach
problem and not a capability one (**N-453**, ruled to `balance:X-f4`). The runbook prescribes clicks,
so it takes the unarchive.

**The order does not change, and the reason is NOT the double count.** A first draft of this
paragraph said 4c opens the double-count window and that taking it early would widen it. Both halves
were wrong, and the stop rules above say so: that window opens at **act 3**, which is what puts the
money inside account 10's opening while the twin still asserts `$5,363.56` -- 4c neither opens nor
closes it, and only 4d does. Nor would taking 4c early widen the exposure: the rule ranks ARCHIVED
as the narrower state (only the balance sheet sees it) and the UNARCHIVE as what puts it on every
dashboard, so doing 4c while archived is if anything the quieter half. Corrected by adversarial
review, 2026-09-04.

**What 4c DOES open is smaller and separate, and it is still a reason to keep the order.** The
restatement takes the twin's opening to `$0.00` while its 2026-04-06 assertion stands, so the gap
books a `$5,363.56` correction against that assertion -- and ruling **R-FO** sends an
interest-bearing account's true-up counter leg to `interest_income`, so the income statement reports
a gain that never happened until 4d clears it. That window opens at 4c and closes at 4d, and the
income statement carries no archived-account exclusion to hide it. Since 4d forces the unarchive
anyway, taking 4c early buys one click and pays for it by running that phantom gain for however long
separates the two sittings -- against a stop rule that already requires 4c, 4d and the re-archive in
one sitting.

Unarchive it from the cockpit's archived region, do 4c and 4d, then archive it again. **Do not stop
while it is unarchived** -- see the stop rules above. Each flip moves no money, and that is GRADED
rather than claimed: the rehearsal digests every posted-ledger row either side of each archive act
and refuses if the digest moves. `tests/manual/rehearse_account_10_repair.py` rehearses THIS order.

**4c.** Account 2 -> **Edit** -> **Books opening**. Day **2026-03-26**, equity **`$0.00`**.

**4d.** Account 2 -> its balance editor. Assert **`$0.00`** observed on **2026-04-06**.

That day already carries an assertion of `$5,363.56`. You are not editing it -- assertions are
append-only at the database tier -- you are stating a newer one for the same day, which supersedes it.
The old figure stays in the account's history, which is correct: it is what you believed at the time.

*What you should see:* the twin's balance goes to `$0.00` -- the one act in this repair whose effect
is immediately visible as a balance -- and the balance sheet stops carrying an asset for an account
holding nothing. That is **N-384**'s instance discharged.

### Act 5 -- record the five dividends

**First create the category.** Settings -> Categories -> new, group **Income**, item
**Interest & Dividends**. Ruling **R-HL**: real investment income has never had a category of its
own, so today it is absorbed by balance true-ups whose counter leg books to the account's modelled
`interest_income` row. A category is owner data and costs no code.

Then, on account 10, for each dividend: create an `Income: Interest & Dividends` row for the amount,
mark it Received, then reopen the settled card and set **Money moved on** to the real day. **Finish
each row's three saves before starting the next** -- between the second and third the amount sits in
your balances on today's date, exactly as in act 4b.

| day | amount | pay period starting |
|---|---|---|
| 2026-03-31 | `$13.35` | 2026-03-26 |
| 2026-04-30 | `$15.70` | 2026-04-23 |
| 2026-05-29 | `$15.01` | 2026-05-21 |
| 2026-06-30 | `$15.24` | 2026-06-18 |
| 2026-07-31 | `$14.39` | 2026-07-30 |

Each is one `DIVIDEND RECEIVED` line in the export. The `REINVESTMENT` line beside it is the same
money buying the core position back and is not a second event. The export carries seven such lines;
the 2026-01-30 `$3.82` and 2026-02-27 `$4.47` are on or before the opening day and are inside the
opening equity.

*What you should see:* account 10's modelled `interest_income` row empties -- it carried `-$30.25` of
corrections, which had been standing in for the **05-29 and 06-30** dividends only -- and
`Income: Interest & Dividends` carries `$73.69` of income. Total income moves `$43.44`, which is the
`$73.69` recorded less the `$30.25` of modelled interest it replaces.

### Act 6 -- assert the bank's last stated close

Account 10 -> its balance editor. Assert **`$3,673.90`** observed on **2026-07-31**.

Ruling **R-HM**: the modelled accrual window opens at the latest assertion, so without this the
2026-07-31 dividend sits inside the open window and is counted twice. Asserting the bank's own close
for the last day the export covers moves the window past it.

---

## What you will and will not see move

**Most of these acts move a CORRECTION, not a balance, and an operator who does not know that will
think the repair is doing nothing.** Every account here carries balances you typed, and an assertion
RESETS the running total on its own day -- so a movement re-dated below the latest assertion changes
what the records EXPLAIN without changing what the account SHOWS.

Measured over the rehearsal, valuing at 2026-07-31 (the last day the export states -- a fixed point,
where "today" would give a different number every day the repair is run):

| after | Checking | the twin | account 10 | account 10's corrections |
|---|---|---|---|---|
| *before act 1* | `$1,307.66` | `$5,420.42` | `$3,666.11` | `$30.25` |
| act 1 | `$1,307.66` | `$5,420.42` | `$3,666.11` | `$530.25` |
| act 2 | `$1,307.66` | `$5,420.42` | `$3,665.93` | `$530.25` |
| act 3 | `$1,307.66` | `$5,420.42` | `$3,665.93` | `$59.30` |
| act 4 | `$1,307.66` | **`$0.00`** | `$3,665.93` | `$59.30` |
| act 5 | `$1,307.66` | `$0.00` | `$3,680.32` | **`$0.00`** |
| act 6 | `$1,307.66` | `$0.00` | **`$3,674.22`** | `$0.00` |

**Checking's balance never moves at this date** because its own 2026-07-31 assertion governs and
resets the fold above everything the repair touches. **Its correction on that day does not move
either -- `-$538.29` before and after.** What moves is Checking's corrections on **eight other days**
in April and May, by up to `$2,000.00` on a single one. Figures are the ASSET-side leg -- what each
correction adds to Checking's own balance:

| day | correction before | after | change |
|---|---|---|---|
| 2026-04-06 | `+$491.24` | `-$8.76` | `-$500.00` |
| 2026-04-11 | `+$486.64` | `-$13.36` | `-$500.00` |
| 2026-04-23 | `+$263.34` | `+$2,263.34` | `+$2,000.00` |
| 2026-05-01 | `-$588.40` | `-$2,088.40` | `-$1,500.00` |
| 2026-05-07 | `-$39.89` | `+$460.11` | `+$500.00` |
| 2026-05-10 | `-$297.64` | `-$797.64` | `-$500.00` |
| 2026-05-14 | `+$35.49` | `+$285.49` | `+$250.00` |
| 2026-05-16 | `-$23.05` | `-$273.05` | `-$250.00` |

A ninth entry appears on 2026-03-27, the new expense's own day, and carries `$0.00`. The eight
changes net to exactly **`-$500.00`**: the records now explain `$500.00` more of Checking than they
did, which is **N-382**'s whole exposure. If you check Checking at one date and see nothing, that is
why.

**Do not use "the trial balance is zero" as a check.** It always is: a deferred database trigger
refuses any journal entry whose legs do not sum to zero, so that figure measures the trigger, not
this repair. What the acts actually move is the balance *between* classes:

| class | before | after | change |
|---|---|---|---|
| Asset | `$433,356.21` | `$428,007.04` | `-$5,349.17` |
| Equity | `-$222,997.59` | `-$218,104.98` | `+$4,892.61` |
| Expense | `$25,773.39` | `$26,273.39` | `+$500.00` |
| Income | `-$33,763.17` | `-$33,806.61` | `-$43.44` |
| Liability, Unrealized | -- | -- | `$0.00` |

---

## After: what to check

**Run the measurement again**, the same command as step 3. Rehearsed 2026-09-01:

| what | before | after |
|---|---|---|
| the books open | 2026-04-05 | 2026-03-26 |
| the opening day, books against the bank | `-$484.30` | **`$0.00`** |
| days scored | 13 | 14 |
| exact on the cash fold | 4 | **14** |
| worst gap on the cash fold | `$2,000.00` | **`$0.00`** |
| exact on the rendered figure | 3 | **13** |
| worst gap on the rendered figure | `$2,004.01` | **`$0.32`** |

The scored day count RISES by one because the span starts the day after the books open, and the
repair moves the books back from 2026-04-05 to 2026-03-26.

**The opening-day row is NOT an independent measurement and the rest of the table is.** For the
REHEARSAL, both sides of it come from the export: the harness restates the opening to the export's
own close for that day, and the measurement then compares the stored opening against the same close,
so the arm can only read `$0.00`. What it does grade is that the two programs parse the same figure
and that the door stored what was submitted -- and, when a HUMAN performs the repair, that they
typed it correctly, which is the case that matters here. The 14 scored days are independent: the
span starts the day AFTER the books open, so no act sets what they compare against.

**The `$0.32` is expected and is not a failure.** On 2026-07-31 the cash fold answers the bank's
`$3,673.90` exactly; the figure the SCREEN shows adds one day of modelled accrual on top of a close
the bank has already stated. Ruling **R-HM** names it, and today's 2026-07-16 assertion has the same
property. **It is not a property of this repair** -- it is the accrual window opening on the
assertion's own day, which is a standing `$20.49` overstatement across all five modelled accounts and
has its own fix pending.

**Five bank days are not compared and never can be.** 2026-01-30, 02-24, 02-26, 02-27 and 03-12 are
below the books, and ruling **R-HG** puts them inside the opening equity. The measurement prints
their count and the two endpoints of the range -- not each day -- so this list is the full one.

**Three other things to see:**

- the twin holds `$0.00` and is archived;
- account 10's posted corrections total `$0.00` -- every balance you ever typed for it is explained
  by its own records;
- account 10's modelled `interest_income` chart row carries `$0.00`.

**Today's figure for account 10 will RISE.** Rehearsed on 2026-08-31 it rose `$8.14`; ruling R-HM
quotes `$8.13` from 2026-08-28. Neither simple explanation accounts for the cent -- three days of
accrual on the account is about `$0.97`, and three days on the `$14.39` the repair adds is under a
cent -- so it sits at the rounding floor of two separately-quantized folds and no mechanism is
claimed for it here. **The figure is date-dependent: measure it on the day you perform the repair
rather than comparing against a quoted one.**

---

## What this does NOT fix

- **Checking's own opening is still wrong.** Its books open 2026-03-26 holding `$689.16` where the
  bank's close for that day is `$3,182.63` -- `$2,493.47` apart, which the measurement prints on its
  own line. That is **N-275**, a different account's repair, and it needs a statement import first
  (**N-368**).
- **Two rows stay restorable.** Transfers 1 and 102 are soft-deleted and the recurrence conflict
  chooser can put either back. **N-386**, now binding on two rows rather than one.
- **An archived account still reaches no BALANCE editor, so this procedure still unarchives.** Its
  RESTATEMENT door has a click path as of `balance:X-f3c-2b-2d`, which closed **N-430** -- but the
  archived card's figure is static text, the grid resolves only active accounts, and the three
  anchor editors that remain sit on pages that card links to from nowhere. Reach, not capability:
  `/accounts/2/details` typed directly answers 200 with a working editor. 4d forces the
  unarchive, and act 4 keeps 4c inside it deliberately rather than taking the new link. Whether that
  reach is a defect at all or is what archiving MEANS is **N-453**, ruled to `balance:X-f4` on
  2026-09-04 because it is the same question about the same state as **N-384** below.
- **The class behind the twin survives the instance.** An account archived while its ledger still
  holds a net is a state the app can still reach, and no surface says the balance sheet and the
  dashboards disagree about it. **N-384**, owned by `balance:X-f4`.
- **The `$500.00` of expense act 4b books is not spending, and the developer has now REJECTED that
  method** (2026-09-01) -- so this is no longer a limitation the repair accepts, it is an OPEN act
  that blocks the procedure. **The blocker this document used to cite was also the wrong one**
  (adversarial review, 2026-09-01): it named `pay_calendar:C6` and ledger row `P10`,
  which are both about a payday inserted MID-SCHEDULE, between two existing ones. Neither is a
  backward extension of the calendar, and neither is what would have to change. Nor does the
  restatement door impose a calendar floor at all: `opening_service` says in terms that
  `pay_period_service.earliest_recordable_day` is "deliberately **not** asked" there, because that
  floor is a rule about ASSERTIONS (**R-ER**), and `PayCalendar.filing_period` CLAMPS a pre-calendar
  day onto the earliest period rather than refusing it. What actually rejected the 2026-03-25
  opening is **R-HK**, on its own grounds. A third option nobody has priced: delete transfer 1 and
  book nothing, letting the `$500.00` land as an equity correction against Checking's next assertion
  -- net worth identical, income statement TRUE, at the cost of a real outflow going unrecorded on
  an account whose own records are already `$2,493.47` out (**N-275**).

---

## How this was rehearsed

2026-09-01, on a throwaway clone of production taken to alembic head `e2d7a94f61c3`, driven through
the same HTTP doors this document describes. Most acts fetch the form the owner opens, change only
what they type, and submit the rest exactly as rendered; the two deletes, the archive round trip and
the category create are direct submissions, because those controls are a button with no payload or --
for the category's group -- a hidden input whose value is set by script rather than rendered.

- `tests/manual/rehearse_account_10_repair.py` -- performs and verifies the acts.
- `tests/manual/measure_cutover_against_bank.py --format fidelity` -- scores the result.
- `tests/manual/verify_render_surfaces.py` -- 108 routes, **0 server errors** before and after.

**Six planted defects in the stated transfer map were each refused before any write**: two transfers
exchanged, a duplicate claim on one bank day, a transfer id that does not exist, a day the export
does not name, an amount it did not move, and a bank day left unanswered. A seventh planted an
opening day the door refuses, to confirm the harness notices a refusal the app reports with a
redirect rather than an error code.
