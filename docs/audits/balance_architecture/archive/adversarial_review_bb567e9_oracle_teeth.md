# Adversarial review -- commit `bb567e9` "make the reconciliation-oracle teeth executable" (R5/M4/M5)

Reviewer: second Claude Code session (adversarial watch on `fix/ledger-period-attribution`).
Date: 2026-07-02. Scope: the single commit `bb567e9` (test-only, 3 files, +305/-15).
Method: full diff read + independent source-traced pass + `pylint` on the changed files.
Tests not executed in the review sandbox (no DB socket); the commit claims full suite `6865 passed`.

This file is a follow-up to `adversarial_review_balance_architecture_2026-07-02.md`. It concerns the
executable-oracle work that closes review items **R5 / M4 / M5**. Left for the authoring session to
read and address; nothing here has been changed in the code.

> **AS-BUILT 2026-07-02 (all findings addressed).** The authoring session verified every
> finding against source and addressed each; per-finding `RESOLVED` annotations are inline
> below. Summary: **M-1** fixed (fence extended to cover `loan_payment_service`'s
> resolver-feeding functions at function granularity + a non-vacuity proof); **M-2** fixed
> (coverage guard keeps the token denylist complete; added the missing `pay_period_admin`,
> dropped the dead `posting_infrastructure`); **M-3** addressed (runtime fence relabeled a
> defense-in-depth backstop, its module-qualified-only scope stated honestly); **L-1** fixed
> (`match=` on the three helper-driven tamper proofs); **pylint** C1803 fixed and new code
> written clean; **D-1 / D-2** applied to the companion doc. The "Bottom line" below reflects
> the PRE-fix state (M-1 open) -- read it as the reviewer's original verdict, now closed. Full
> suite 6870 passed; `pylint app/` untouched (test-only + docs). Test changes:
> `tests/test_integration/test_posting_ledger_{loan,cash,}_reconciliation.py`.

---

## Bottom line

The commit is **sound**, and its core protections are **real** (source-verified, not taken on the
commit message's word). **No critical or high defect. The financially load-bearing tooth -- the
exact-value pin on the loan split -- genuinely holds.**

There is **one Medium finding (M-1) worth fixing** before M4a is treated as closed: the new
"resolver-is-ledger-free" fence does **not** scan the module the resolver's reference balance
actually runs through, and that module already reads the ledger -- so a future refactor could
silently collapse the parallel run into a tautology with every test still green. That directly
undercuts the independence guarantee the commit says it now makes "mechanical."

---

## Plain-language context (why any of this matters)

Each loan balance is computed **two independent ways** and asserted equal to the penny: the new
**posting ledger**, and the legacy **resolver** (which derives balance from the payment schedule +
transactions). That two-way agreement is the safety net that keeps a loan balance from drifting.

The net only means something if the two methods are **actually independent**. If the "independent"
resolver secretly reads the ledger, then "they agree" is guaranteed regardless of correctness -- a
*tautology* -- and proves nothing. This commit adds **fences**: automated tests that fail if anyone
wires the ledger into the resolver, so the independence can't rot unnoticed. It also promotes the
"+$10 interest injection" experiment from commit-message prose into an executable regression test.

---

## What genuinely bites (source-verified)

- **+$10 walk injection is a real, exact-value tooth.** The patch target
  `app.services.loan_posting_service._walk.accrue_monthly_interest` is the exact binding the walk
  uses (`app/services/loan_posting_service/_walk.py:44` import, `:206` open-loan split call). The
  resolver accrues through a *distinct* import (`app/services/rate_period_engine.py:52`, used at
  `:414`/`:598`), so patching the walk binding cannot reach the resolver. The delta is asserted as an
  exact value (`ledger == resolver + Decimal("10.00")`, same for `reader`); a no-op patch would
  *fail* that assertion rather than pass vacuously. `_assert_loan_reconciles` uses only structural
  identities / production readers, never a resolver-vs-reader value compare, so its survival under
  the injection is correct by design, not luck.
- **Non-empty guards are real and correctly placed** on all three swept enumerations
  (`_assert_full_reconciliation`, `_assert_linked_accounts_reconcile`, `_assert_completeness`): each
  guards a loop that would otherwise iterate empty.
- **Tamper-through-real-helper proofs bite.** In each tamper test the drift is `commit()`-ed *before*
  the `pytest.raises`, so the helper re-derives against live drifted data and genuinely raises.
- **AST negative control bites.** `_ledger_imports_in_source` flags all four import shapes
  (`from pkg.mod import x`, `from pkg import mod`, a ledger model, plain `import`) and returns `[]`
  on clean source; aliased imports are caught (the alias carries the real dotted path).

---

## M-1 (MEDIUM) -- the ledger-free fence has a structural blind spot on the resolver's real path

> **RESOLVED 2026-07-02** (fix option (a), test-only). The AST fence now covers the
> resolver reference's WHOLE path, at the granularity each module allows.
> `loan_loaders` + the `loan_resolver` package stay FILE-fenced; the mixed
> `loan_payment_service` is now fenced at FUNCTION granularity via
> `_loan_payment_service_resolver_feeding_source()`, which excises the read-switch
> functions' source (`_LEDGER_READ_SWITCH_FUNCTIONS` = `confirmed_loan_view` /
> `resolve_loan_seeded` / `resolve_account_loan`) and hands the remainder --
> `load_loan_context` and its sibling loaders (`get_payment_history` /
> `compute_contractual_pi` / `prepare_payments_for_engine`), PLUS the module's
> top-level imports -- to the same `_ledger_imports_in_source` scan. A newly added
> function defaults into the SCANNED set (the safe polarity), so wiring the ledger
> into a resolver-feeding loader here now fails the fence. Excising by source-segment
> (not name-scan) keeps top-level imports in scope, so a ledger import added at module
> top is caught too. Option (b) (splitting the production module) was rejected as
> disproportionate: it would edit the carefully-documented loan read path to serve a
> test fence, against the stay-in-scope / no-gold-plating rules, for a test-only
> hardening commit. Non-vacuity is pinned by
> `test_loan_payment_service_function_fence_is_scoped_and_bites`: it proves the
> loaders are in scope, the read-switch functions are held out, and the WHOLE module
> DOES trip the fence (a real ledger read) while the feeding remainder does not -- so
> the pass is around a genuine target, not a trivially-clean module.

**Where:** `tests/test_integration/test_posting_ledger_loan_reconciliation.py`,
`_resolver_stack_modules()` and its docstring ("every module the un-seeded resolver reference is
built from ... the modules `_resolver_balance` runs through").

**Defect:** `_resolver_balance` calls `loan_payment_service.load_loan_context`, but
`_resolver_stack_modules()` returns only `loan_loaders` + the `loan_resolver` package.
`loan_payment_service` is **not** in the fenced set -- and it *cannot* be added, because it already
imports a ledger reader (`app/services/loan_payment_service.py:495-501`, `loan_posting_service` in
`resolve_account_loan`). The file-granularity fence structurally cannot cover a module that mixes
ledger-reading and resolver-feeding code.

**Slip-through scenario:** a refactor that makes `load_loan_context` (or `get_payment_history` /
`load_active_escrow_components` / `prepare_payments_for_engine`, all in that module) read the
confirmed-ledger balance via a *name* import would taint the resolver reference. **Neither fence
catches it** -- the AST fence never scans `loan_payment_service`, and the runtime fence (M-3) only
intercepts module-qualified calls to 5 hard-coded symbols. The parallel run collapses to a tautology
and every test stays green. That contradicts the commit's "mechanically guaranteed independent"
claim for M4a.

**Fix options:** (a) extend the fence to scan `load_loan_context`'s call graph explicitly, or (b)
split the ledger-free loaders out of the mixed `loan_payment_service` module so the file-granularity
fence can cover them cleanly.

---

## M-2 (MEDIUM) -- token allowlist, not a real module fence

> **RESOLVED 2026-07-02.** The denylist is now backed by a coverage guard,
> `test_ledger_import_tokens_cover_every_ledger_reader`. It discovers -- from source
> on disk, no imports -- every `app.services` module that imports a posted-ledger
> MODEL (`Posting` / `JournalEntry` / `LedgerAccount`, the objective definition of
> "reads the posted ledger"), maps each to the top-level name a by-name import would
> carry, and fails if any is not matched by a `_LEDGER_IMPORT_TOKENS` substring. So a
> new reader named e.g. `confirmed_reads.py` now fails THIS test (loud) until a
> covering token is added, instead of silently evading the resolver fence. Building
> the guard empirically confirmed M-2: the pre-existing list missed `pay_period_admin`
> (a real ledger-model importer), now added; the dead `posting_infrastructure` token
> (matched no module) was dropped. The denylist stays readable and intentional, but is
> no longer an unverified guess -- the guard keeps it complete.

`_LEDGER_IMPORT_TOKENS` is 8 fixed name-substrings. Modules whose names lack every token evade it:
verified that `app.services.ledger_reads`, `debt_ledger`, and `account_balances` would all pass
undetected. The commit sells the scan as "no resolver-stack module may import a posted-ledger
module"; it is really "no module importing a name containing one of these 8 substrings." A new
ledger reader named e.g. `confirmed_reads.py`, imported into a resolver submodule, stays green.

---

## M-3 (MEDIUM) -- runtime read fence asserts nothing about present code, narrow regression teeth

> **RESOLVED 2026-07-02** (honesty fix; the narrowness is inherent to
> `monkeypatch.setattr` and is now stated, not overstated). The runtime fence's
> docstring no longer claims "every posted-ledger reader a refactor might reach for."
> It now states plainly that (1) because the resolver is ledger-free today the pass
> gives NO signal on current code -- the STATIC fence proves that -- and (2) as a
> regression guard it catches only a MODULE-QUALIFIED call to the listed symbols
> through the patched module object; a name-import binding or an unlisted reader would
> NOT fire here, which is the static fence's job. It is relabeled a defense-in-depth
> backstop, not the load-bearing guarantee. With M-1 closed, that static fence is now
> path-complete over the resolver reference (including `loan_payment_service`), so the
> shapes this runtime check cannot see are covered by the fence that can.

`test_resolver_balance_reads_no_ledger_at_runtime`: because the resolver is ledger-free today it
never calls the 5 patched readers, so `_resolver_balance() == expected` passes regardless of whether
the resolver reads the ledger -- the test gives no signal on the current code. As a regression guard
it only bites a *module-qualified* call to those 5 exact symbols through the patched module objects;
a name-import binding (the common shape, and the one `resolve_account_loan` proves is easy to
introduce) escapes it, as does any new reader not in the list. The docstring's "every posted-ledger
reader a refactor might reach for" overstates this. (All 5 targets do exist, so there is no silent
no-op from a bad patch target -- verified.)

---

## L-1 (LOW) -- `pytest.raises(AssertionError)` with no `match=`

> **RESOLVED 2026-07-02.** The three tamper proofs that drive a real multi-assertion
> sweep helper now pin the INTENDED comparison with `match=`: the loan superseding
> invariant (`match="non-principal corrections"`), the cash linked-account reconcile
> (`match="combined source effect"`), and the transfer per-account reconcile
> (`match="settled-shadow effect"`). Each substring is unique to the invariant the
> proof means to lock, and does NOT appear in the non-empty guards or the
> trial-balance message -- so a future edit that weakened THAT comparison while some
> other assertion still fired under tamper no longer keeps the test green. Verified by
> run: the proofs pass, which under `pytest.raises(..., match=...)` means the raised
> message actually matched the pinned pattern. The two remaining bare
> `pytest.raises(AssertionError)` blocks in the +$10 walk test wrap a single
> self-contained `assert ledger == resolver` (no helper, only one thing can raise), so
> the ambiguity L-1 describes does not apply and they are left as-is.

The tamper proofs and the value-check blocks catch **any** `AssertionError`. Each helper now
contains 4-5 assertions, including the just-added non-empty guards, which also raise
`AssertionError`. They fire for the right reason today, but a future edit that weakens the *intended*
comparison (e.g. `==` -> `>=`) while any other identity or the non-empty guard still fires under
tamper keeps the test green -- so the specific tooth this commit means to lock can be lost
undetected. Add `match=` pinned to the intended assertion's message.

---

## Pylint (informational -- tests are NOT in the enforced `pylint app/` floor)

> **ADDRESSED 2026-07-02.** `C1803` was fixed (`_ledger_imports_in_source(...) == []`
> -> `not _ledger_imports_in_source(...)`), and the new fence code was written
> pylint-clean (the follow-up's two new findings -- `C0207` use-maxsplit and a
> `W9906`/`W9902`-adjacent false positive on `alias.name` vs a string literal -- were
> resolved before commit: `maxsplit=1` and moving the leaf names into the
> `_LEDGER_MODEL_NAMES` constant so the refname checker sees a name, not a literal).
> The `R0913`/`R0917` too-many-arguments on pytest fixture-param methods are left as
> the review notes -- they match ~10 pre-existing instances the project does not gate,
> and reducing them would drop needed fixtures.

No `app/` files changed, so the CI gate (`pylint app/`) is untouched by this commit. On the test
files themselves this commit adds: `C1803` at `:1502` (`_ledger_imports_in_source(...) == []` ->
`not ...`) and `R0913`/`R0917` too-many-arguments (6/5) at `:1315` and `:1508` (pytest fixture
params; matches ~10 pre-existing instances in these modules, which the project does not gate).
Cross-file `duplicate-code` (R0801) reported on these files is pre-existing scenario-isolation
boilerplate, not introduced here.

---

## Checked and clean

- No weakened assertions: all three diffs are additive; the only deletions are docstring prose. No
  tolerance loosening, no `==`->`>=`, no exact-value->`is not None` anywhere.
- No new empty-loop vacuity: the only new enumeration (`_resolver_stack_modules()`) is structurally
  non-empty and negative-controlled.
- The `+$10` patch cannot pass for the "patch is a no-op" wrong reason -- it would fail the
  exact-value assert.

---

## Companion docs commit `3e580c3` "annotate the review with R5 as-built status"

`3e580c3` (docs-only, landed immediately after `bb567e9`) marks R5 DONE and records M4(a)/M5 as
resolved in `adversarial_review_balance_architecture_2026-07-02.md`. Reviewed for accuracy against
the implementation audited above. Two Low-severity overclaims, recorded here because this is the
authoritative status ledger.

### D-1 (LOW, substantive) -- the M4 annotation repeats the M-1 independence overclaim

> **RESOLVED 2026-07-02.** With M-1 now fixed the blind spot is CLOSED, not merely
> noted: the companion doc's M4 annotation and R5 table row were updated to record
> that the fence covers `loan_payment_service`'s resolver-feeding functions at
> function granularity (holding out the read-switch functions), so a refactor wiring
> the ledger in through `load_loan_context` now DOES fail a test. The annotation no
> longer reads as airtight-by-file-fence; it names the function-granularity coverage
> and the M-2 coverage guard explicitly.

The M4 annotation states: "a future refactor that let the resolver consult the ledger fails a test
instead of silently collapsing the parallel run to a tautology." That is stronger than what shipped.
The same annotation correctly scopes the fence to "`loan_loaders` + the dynamically-enumerated
`loan_resolver` package" -- but that scope excludes `loan_payment_service`, which `_resolver_balance`
runs through (`load_loan_context`) and which already reads the ledger (see M-1 above). A refactor
wiring the ledger in *through that module* would NOT fail a test. Recommend the M4(a) annotation and
the R5 table row note the `loan_payment_service` blind spot so M4(a) is not recorded as airtight.

### D-2 (LOW, trivial) -- stale suite count in the header summary

> **RESOLVED 2026-07-02.** The companion doc's header count was corrected. It no
> longer pins a stale "6861 at R5"; it now records the current authoritative full
> suite (6870 passed) and notes R5's own figure was 6865. This follow-up's M-1/M-2
> fixes added 2 tests (the function-fence non-vacuity proof and the token coverage
> guard), and R7 had already moved the count past R5's 6865, so 6870 is HEAD.

The header now reads "M5 and M4(a) are fixed (R5: ...). Full suite 6861 passed". R5 added 4 tests
(the +$10 test + 3 in `TestResolverIsLedgerFree`); `bb567e9`'s own message says 6865 passed
(`6861 + 4 = 6865`). The header's `6861` is stale relative to the scope it now claims.

No code risk in `3e580c3`; documentation accuracy only.
