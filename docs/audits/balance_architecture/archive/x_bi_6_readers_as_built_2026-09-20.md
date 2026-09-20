> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is `docs/plans/steps.md`; the code as
> committed is the source of truth for what the app does.

# X-bi-6-1 and X-bi-6-1b as built: every DISPLAY reader draws a transfer as a LEG (2026-09-20)

Archived under rule 5 at the tick of the two reader leaves (6-1 `45742c05`, 6-1b `6773418b`) that opened the
X-bi-6 family (**R-BAL86**, five leaves; **R-BAL87**, the leg's identity). The commits are the record; the
build logs, the byte-identical harness outputs (`verify_balance_baseline.py`, `verify_render_surfaces.py`,
`verify_grid_cells.py`, the leaf's own `tests/manual/verify_reader_content.py`) and the two adversarial
reviews are `~/projects/shekel-handoffs/HANDOFF-X-bi-6.md` and `balance-2026-09-20/xbi6-1/`, `xbi6-1b/`.
Neither leaf carries a migration or moves money. Below: the X-bi-6 entry as it stood in the README before
the split (verbatim; the `[ ]` is the entry before the tick), then the ledger rows 6-1 closed.

## The X-bi-6 entry as it stood before the split, verbatim

  * [ ] **X-bi-6** delete the transfer shadow `Transaction` ROWS (**R-BAL13**, superseding this
    step's stored-column scope under **R-JA**). Its two columns are two of the **FIVE** clauses
  `restore_transfer` keeps by hand -- `pay_period_id`, `category_id`, `due_date`, `is_override`,
    `status_id` (`_restore.py:144-221`) -- and the fence is wider than the 20 sites first counted: the Python branches are the census markers on its `steps.md` row (re-run at every tick), plus 7 Jinja in 3 templates, 4 inverted guards, 7 query exclusions. **The pair-drift
    repairer is `transfer_service._restore`, NOT `posting_service`**, which only skips and warns --
  deleting the latter removes a skip arm and leaves the repairer standing. The `NO ACTION` restore this entry once carried has no object since the 2026-09-18 re-mint (`credit_card:CC-5` re-cuts that key to a plain FK; its row is **CC-353**, renamed from BAL-506). Closes **BAL-503**. **Still after X-bi-4:
    INVARIANT 5 IS WHY THE MIRROR EXISTS.** Closes **BAL-475**.

## Ledger rows CLOSED at this tick (moved here under the same id)

**N-303** and **N-420** CLOSED at `balance:X-bi-6-1` `45742c05`: the transfers page's cell
(`transfers/_transfer_cell.html`, `render_transfer_cell`) reads the pair's `settled` / `retained` maps through
the leg producers -- one walk with the grid cell -- and prints its figure through the ONE `money()` formatter.
The rows as they stood:

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| balance | N-303 (X-au-c3's adversarial review 2026-08-18) | -- | **The transfers PAGE's cell response paints the PLAN over a figure just corrected.** `_render_post_mutation_cell` renders `transfers/_transfer_cell.html` when the request carries no `source_txn_id` -- which is what the popover served by `transfers.get_full_edit` submits -- and that template prints `xfer.amount` unconditionally and is passed neither `settled` nor `retained` | `$0.00` and UNREACHABLE from the UI today: nothing in `app/templates/` emits an `#xfer-cell-<id>` element and nothing calls `transfers.create_ad_hoc`, so the live door is the grid shadow cell, which renders the transaction cell and is correct. The suite drives it as a live door | **OPEN, born with an owner.** It is the "one row, two figures on two surfaces the same click opened" shape `RenderAmounts` exists to prevent, so it is fixed where the transfer render paths are rebuilt rather than by threading two maps into a template with no caller **RE-POINTED 2026-09-11 when `X-au-f` ticked, and half the finding text above is already stale**: the cell prints `budgets[xfer.id]` since `X-au-f-1`, not `xfer.amount`. What SURVIVES is the whole defect -- the template is still passed neither `settled` nor `retained`. `X-bi-6` rebuilds these render paths | X-bi-6 |
| balance | N-420 (`bank_import:X-gj-2b-3`'s reader census 2026-09-01) | -- | **The transfers page's cell prints money through a raw format string, bypassing the ONE template currency formatter.** `transfers/_transfer_cell.html:48` renders `{{ "{:,.0f}".format(xfer.amount) }}` where gate B7 makes `money()` that formatter -- the same bypass `grid/_transaction_cell.html` carried on four figures until `bank_import:X-gj-2b-3` routed them through the macro and the developer ruled the currency mark in (**bank_import:R-IM**) | `$0.00`, and the digits are right: a transfer's amount is a stored magnitude that no entry sum reaches, so ruling **bank_import:R-II** cannot make it signable and the two spellings render identically. What differs is the CURRENCY MARK -- this cell prints `1,234` where every other money surface prints `$1,234` -- and that a second spelling of the formatter exists at all, which is what the gate is about | OPEN, born with an owner. **Outside `bank_import:X-gj-2b-3`'s census (rule 6)**: that step's premise is a negative `transaction_entries.amount`, and no entry reaches this cell. **`balance:N-303` already names this template, this rendering path and this owner** for a different defect, so the two are repaired in one pass rather than twice **RE-POINTED 2026-09-11 when `X-au-f` ticked; the cited line has moved** to `_transfer_cell.html:61` and now reads `budgets[xfer.id]`. The `money()` bypass itself survives. Follows `N-303` to `X-bi-6`, in one pass, as this row already said | X-bi-6 |
