# Financial-Calculation Audit -- Remediation Follow-up Work

Tracks structural improvements identified during the audit's remediation
commits that are deliberately out of scope for the remediation plan
itself.  Each entry is a self-contained refactor that improves
maintainability without changing financial behavior.  Add work here when
it surfaces; pick from here after Commit 37 (the remediation final
gate) closes.

Cross-references:

- Remediation plan: `remediation_plan.md`
- Commit prompts: `remediation_commit_prompts.md`
- Findings register: `08_findings.md`

---

## F-1. Split `app/routes/accounts.py` into per-domain blueprint modules

- **Surfaced during:** Commit 7 (`fix(accounts): route /accounts checking
  detail through canonical producer (E-25)`), commit `6c09ae8`.
- **Status:** not started; defer until after Commit 37.

### Problem

`app/routes/accounts.py` is 1,499 lines and breaks the project's 1,000-
line per-module ceiling (pylint `C0302 too-many-lines`, pre-existing
warning).  The file holds 21 endpoints spanning five distinguishable
sub-domains separated by banner comments today, which makes the file
hard to read end-to-end and creates merge-conflict surface for every
audit commit that touches it.

### Current sub-domains (banner-delimited in the file)

| Sub-domain | Routes | Lines (approx) | Templates | External coupling |
|---|---|---|---|---|
| Account CRUD | `list_accounts`, `new_account`, `create_account`, `edit_account`, `update_account`, `archive_account`, `unarchive_account`, `hard_delete_account` | ~525 | `accounts/list.html`, `accounts/form.html` | Heavy: `account_service`, `transfer_service`, `pay_period_service`, optimistic-lock contract, the `_validate_update_account` helper, the `_account_type_is_visible` helper |
| Inline anchor edit (list) | `inline_anchor_update`, `inline_anchor_form`, `inline_anchor_display` | ~145 | `accounts/_anchor_cell.html` | Shares anchor-history idempotency machinery with grid `true_up` (F-103 / C-22) |
| Account Type CRUD | `create_account_type`, `update_account_type`, `delete_account_type` | ~160 | `settings/*` | Independent (C-28 multi-tenant guard only); does not touch `Account` rows |
| Anchor true-up (grid) | `true_up`, `anchor_form`, `anchor_display` | ~180 | `grid/_anchor_edit.html` | Shares the same idempotency helper and history-row machinery as `inline_anchor_update`; HX-Trigger to `balanceChanged` |
| Detail pages | `interest_detail`, `update_interest_params`, `checking_detail` | ~255 | `accounts/checking_detail.html`, `accounts/interest_detail.html` | Now routed through `balance_resolver` (Commit 7); Commits 8-10 touch sibling detail surfaces |

### Shared module state to re-home

- `_ANCHOR_HISTORY_UNIQUE_INDEX` constant (the F-103 / C-22 idempotency
  backstop), referenced by both anchor-update endpoints.
- `_visible_account_types`, `_owned_account_type`,
  `_validate_update_account`, `_account_type_is_visible` helpers (the
  C-28 multi-tenant ownership machinery).
- Six Marshmallow schema singletons (`_anchor_schema`, `_create_schema`,
  `_update_schema`, `_type_create_schema`, `_type_update_schema`,
  `_interest_params_schema`).
- The `accounts_bp` blueprint -- all 21 routes register against it.

### Two split options

- **Option A (single blueprint, file split by import).** Estimated
  effort: 1-2 days.  Keep one `accounts_bp` blueprint; split routes
  across `app/routes/accounts/__init__.py` (registers the blueprint),
  `accounts/crud.py`, `accounts/anchor.py`, `accounts/types.py`,
  `accounts/detail.py`.  Each file does `from . import accounts_bp` and
  registers its own decorators.  URLs unchanged; no `url_for` call
  sites edited.  This is the recommended option.
- **Option B (per-sub-domain blueprints with new `url_prefix`s).**
  Estimated effort: 3-4 days.  Adds the cost of updating every
  `url_for("accounts.X")` reference in routes, templates, JS, and
  tests.  ~50 references in routes, ~100-150 in templates and tests.
  High blast radius, all mechanical, but no organisational gain over
  Option A.

### Effort breakdown (Option A)

- 4-6h: factor the shared anchor-history idempotency helper (the
  `IntegrityError + uq_anchor_history_account_period_balance_day`
  handler that lives inline in both `inline_anchor_update` and
  `true_up`) into one named helper in `app/services/entry_service.py`
  or a new `app/services/anchor_service.py`.  The DRY win lands here;
  can be verified before any file moves.
- 2-3h: move helpers and schema singletons into
  `app/utils/account_validation.py` (or similar) and update imports.
- 3-4h: physically split into the per-sub-domain files.
- 1-2h: pylint, run the full suite, fix any forgotten internal
  references (e.g. `app.routes.accounts.<symbol>` test imports).

### Why defer until after Commit 37

1. The split is mostly mechanical movement, but every remaining audit
   commit that touches this file would have to be rebased through the
   split if it lands first -- doubling the merge-conflict surface for
   limited benefit.
2. The first step of the split (extracting the shared anchor-history
   idempotency helper) is a finding-adjacent DRY win and could be
   folded into Commit 16 (loan principal true-up) where the same
   shape is implemented; landing the highest-value extraction without
   taking on the full file split.
3. After Commit 37 the split becomes a single self-contained refactor
   PR with no audit-fix interaction.

### Remaining remediation commits that still touch this file

Quoted here so the split planner knows what conflicts with what:

- Commit 8 -- does NOT touch this file (year-end / net-worth /
  investment / retirement live in their own modules).
- Commit 11 -- test-only; consumes `/accounts/<id>/checking` through
  the test client, no source edit here.
- Commit 15 -- demotes loan param columns; touches loan consumers in
  `app/routes/loan.py`, not this file (no cross-leakage confirmed).
- Commit 16 -- loan principal edit as a dated true-up; implementation
  in `loan.py`.  The existing checking-anchor true-up machinery in
  this file is the reference implementation Commit 16 reads.
- Commit 21 -- semantic `is_settled` hard-delete guard.  Touches
  `archive_helpers` and the hard-delete path; the `hard_delete_account`
  route here is a consumer of those helpers and may need a touch-up.
- Commit 24 -- Marshmallow / DB CHECK reconciliation.  The schema
  singletons here would be re-read against the constraint changes.

### Acceptance criteria for the eventual split PR

- `pylint app/routes/accounts*` shows no `C0302` warnings.
- Every `url_for("accounts.X")` reference still resolves (Option A:
  trivially true; Option B: requires audit of all references).
- The full pytest suite passes with no test edits.
- The F-103 / C-22 anchor-history idempotency handler lives in exactly
  one place (the DRY violation that motivated the split is resolved).

---

<!-- Add new follow-up entries above this line, numbered F-2, F-3, ... -->
