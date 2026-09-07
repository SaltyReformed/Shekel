# The MATCH consent, and the member cap: the X-gi-2a fork

**This document is a HOLDING PLACE, not a plan of record.** It carries the developer's ruling of
2026-09-06 verbatim until the registry pass transcribes it into `docs/plans/rulings.md` as
**`bank_import:R-BI2`**, and mints `X-go` and `X-gp` in `docs/plans/steps.md`. It exists because the
ruling was given in a session and a ruling that lives only in a transcript is one context clear from
being lost. **When the registry pass lands, this file's ruling text is superseded by `rulings.md`
and this file should be deleted.**

Ids reserved by the coordinator for this work: ruling **`R-BI2`**; steps **`X-go`** and **`X-gp`**;
findings **`BI-481`**, **`BI-482`**, **`BI-483`**.

## What was asked

Plan step `bank_import:X-gi-2a` prices the Reconcile page's own MATCH pane from the SUBMITTED body
when there is one, so that a refused Apply answers with the owner's ticks rather than the tier's
proposal. That mechanism was already settled and is not what was ruled. What was put to the
developer is that **building it as specified ARMS two defects it does not cause**.

He refused the first framing of both questions -- "these all seem like different ways of hiding the
problem, what is the root cause solution" and "why is the ceiling 100 members, that seems like the
real problem, do it the right way the first time" -- so what follows is the re-derived answer, not
the options originally offered.

## Root cause 1: the consent witnesses a projection of the act, not the act

The pane draws **two controls for one decision**:

- `difference_on-<line>`, a `<select>` -- *Where the difference goes*
- `residual-<line>`, a checkbox whose VALUE is a figure and whose LABEL is a sentence composed from
  the select's server-side value at the moment the page is drawn

`_variance._reject_unaccepted_difference` compares only the FIGURE. So two different acts submit the
same consent.

**Worked example.** Bank line `-$793.23`; ticked rows `Payback in this period -$93.23` and
`Payback an earlier period -$650.00`; app side `-$743.23`; difference `-$50.00`.

| | the owner leaves the select alone | the owner names the `-$650.00` row |
|---|---|---|
| what is written | a NEW uncategorised row of `-$50.00` is minted | nothing is minted |
| the owner's rows | both untouched | `Payback an earlier period` re-priced `-$650.00` -> `-$700.00` |
| what is submitted | `residual = -50.00` | `residual = -50.00` |

The second figure is `DifferenceLanding`'s own arithmetic: what the bank moved, less what the OTHER
members come to, `-793.23 - (-93.23) = -700.00`.

Ruling **`R-IV`** (2026-09-01) already accepted that bound, on the VERIFIED ground that a browser
cannot produce it: the attribution select sits inside `.rec-match-picks`, whose change swaps
`closest .rec-match` with `outerHTML`, and the consent box is inside that element -- so changing
where the money lands always re-renders the box UNTICKED.
**That is a DOM event holding a money consent together**, which is the fence shape `CLAUDE.md`'s
design doctrine says to delete rather than document.

**`X-gi-2a` is what makes it reachable.** On the scriptless `?open=` render nothing swaps. It is
harmless today only because the page prices its pane from `proposed_submission` -- a tier's
proposal, always exact or one-to-one -- so `HandTotals.choices` is empty and the select never
renders at all. Pricing from the SUBMITTED body is what first lets a hand-built multi-row group with
a non-zero difference reach that render. Then an ordinary scriptless owner picks a row in the select
and ticks a consent box whose label describes a different act.

**Two docstrings name the wrong step for this**, which is finding **`BI-481`**:
`_variance._reject_unaccepted_difference` and `_opened`'s module docstring both carve out the
`?open=` pane and say **`X-gn`** must re-read the guard. The step that re-arms it is **`X-gi-2a`**,
which ranks earlier.

## Root cause 2: the member cap is a resource fence wearing a domain rule's clothes

`_MAX_MATCH_MEMBERS = 100` capped both `line_ids` and `rows` on `StatementMatchSchema`. Its stated
reason was that without it "a crafted submission could ask the accept door to re-derive and settle
an account's whole history in one request". Both halves were already false:

- the DOMAIN bound exists where the offer set does. `_resolve.resolve_rows` looks every submitted
  row up in the pass's own `unmatched_rows` and refuses anything else BY NAME; `_resolve.load_lines`
  does the same for lines. A body reaches at most what the pass offered, which the server derived.
- the RESOURCE bound exists where the body does. `MAX_CONTENT_LENGTH` (512 KB, `app/config.py`) is
  checked before the schema runs, and `MAX_BATCH_ITEMS` bounds the ACTS.

**And it had begun to contradict the screen**, which is what made it a defect rather than merely
redundant: since `X-gi-1` the scriptless MATCH pane offers EVERY unexplained row on the account as a
tickbox (`MatchReach.EVERY_ROW`), bounded only by the span the recorded lines cover -- so an account
holding more than 100 of them renders controls the grader refuses, which is ruling **`R-HW`**'s
*a control that cannot succeed*. 67 rows on the developer's own account when the pane was measured
(2026-08-30), so it had not yet bitten; the list grows with the statement span.

## The ruling (developer, 2026-09-06)

> I approve your from scratch plan X-go (delete the cap) -> X-gp (merge the two controls,
> superseding R-IV) -> X-gi-2a

Which settles three things:

1. **`X-go`** deletes `_MAX_MATCH_MEMBERS`. Two bounds, each where its own truth lives.
2. **`X-gp`** replaces the two controls with ONE control whose options ARE the acts, each labelled
   with the figures it writes, its value carrying BOTH the landing and the figure, with the door
   comparing it whole. This **supersedes `R-IV`**, which explicitly rejected widening the consent
   value -- on the premise that the only traveller is someone deliberately crafting a body, which
   `X-gi-2a` falsifies. It changes the wire format of a field two surfaces submit and one strict
   reader grades, and it MOVES MONEY, so it takes its own PR and its own adversarial review.
3. **`X-gi-2a` is blocked on both** and ranks after them. Building it first ships a screen that
   contradicts itself, and every interim wording considered was rejected as hiding the problem.

## The question `X-go` had to answer first, and its answer

A first measurement took the single-item worst case -- a tick is 43 bytes on the wire, so a 512 KB
body carries **12,192** of them -- and wrote it into a docstring as the REASON the cap could go.
**The question it did not ask** is whether `MAX_CONTENT_LENGTH` bounds the whole body or each item,
and therefore whether `MAX_BATCH_ITEMS = 500` items each carrying unbounded members makes the worst
TOTAL multiplicative. Only the per-item reading is multiplicative, and under it a straight delete
would have been the wrong shape. Raised by the coordinator session 2026-09-06.

**ANSWERED: the cap is on the WHOLE BODY, so the worst case is NOT multiplicative and `X-go`'s
straight delete is the right shape.** Every item's ticks come out of one body budget, so 500 items
each naming unbounded members is unconstructible. **The body was always the tighter bound**: 500
items x 100 members is 50,000 ticks against a body carrying at most 22,727, so the deleted cap could
never bind in aggregate and the door's worst case is IDENTICAL before and after this step.

**Two intermediate answers were wrong, and the corrections matter more than the conclusion.** An
adversarial review of the built step found both, before it was committed:

- **The gate named was the one that never fires.** `MAX_CONTENT_LENGTH` (524,288, `app/config.py`)
  is not binding for a urlencoded body. Flask's `MAX_FORM_MEMORY_SIZE` defaults to **500,000**, this
  app never sets it, and Werkzeug's `_parse_urlencoded` weighs the body against that first. The
  codebase already knew: `tests/test_routes/test_auth_validation.py:285` calls it "the WSGI 500KB
  form-size limit".
- **The tick priced was a browser's, while the sentence bounded a CRAFTED body.** A browser emits
  `rows-1234=transaction%3A4567%3A-178.32%3A1&` at 43 bytes; a crafted body pays none of that, and
  `rows-1=purchase:1:1:1&` is **22 bytes** and loads through the live reader and schema. That is
  1.86x more ticks than the first answer allowed for.

Re-measured at the true bound, three runs each:

| shape | ticks | read + grade |
|---|---|---|
| ONE item carrying the whole budget | 22,727 | 50-54 ms |
| 500 items (the ceiling) sharing it | 21,500 | 57-132 ms |

Every row in either is then refused by `resolve_rows`, because no pass offers them. The item ceiling
adds per-item overhead and multiplies nothing.

**A related correction to the domain-bound claim above.** `resolve_rows` is total for what a body
may NAME, but it is not FIRST on the write path: in `_accept.py` the `MatchContent(...)` arguments
evaluate left to right, so `load_lines(..., for_write=True)` takes its row locks BEFORE
`resolve_rows` narrows. Harmless today, because `reconcile_match_payload` emits exactly one
`line_ids` member on every reachable path -- but deleting the cap raises the row-side work done
while holding that lock from at most 100 `repriced` calls to at most the whole offer set (~827 on
the developer's data), which lengthens the window on the already-filed cross-item deadlock finding
**N-471**. Bounded, not a blocker, and stated rather than implied.
