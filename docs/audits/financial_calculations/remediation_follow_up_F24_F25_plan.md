# Financial-Calculation Follow-up Remediation Plan -- F-24 and F-25

Implementation plan that closes the two remaining NOT_DONE items in
`remediation_follow_up.md` after the first follow-up chain
(`remediation_follow_up_plan.md` Commits 1-22) landed. Authored
2026-05-21 against the post-gate `dev` branch.

Cross-references:

- Source entries: `remediation_follow_up.md::F-24`, `::F-25`
- First follow-up plan: `remediation_follow_up_plan.md`
- Shared execution rules: `remediation_follow_up_common.md`
- Main remediation plan: `remediation_plan.md`
- Coding standards: `../../coding-standards.md`
- Testing standards: `../../testing-standards.md`

---

## 0. Context

The first follow-up plan (`remediation_follow_up_plan.md` Commits 1-22)
closed every audit-time NOT_DONE / PARTIAL item except F-24 (templates
/ transfers route-layer duplication, surfaced during the first plan's
Commit 8) and F-25 (pylint R0401 cyclic-import warnings introduced by
the Commit-21 accounts-package split). Both are quality / structural
refactors with no financial-calculation correctness signal -- they fix
pylint warnings that score 0/baseline-impact today but represent latent
hazards that the project's DRY/SOLID standards say must be closed.

Verification of the documented state of both findings against the live
codebase produced the scope additions in Section 3. Both findings'
write-ups in `remediation_follow_up.md` understate the true surface
slightly; the additions are folded into the commit specifications
rather than re-litigated as new F-N entries.

The same constraints that governed the first follow-up plan apply
here:

- You are the only safeguard. CI gates the merge, but a missed
  assertion ships defective behaviour into production.
- Decimal-from-strings for any monetary value; IDs and boolean
  columns for business logic; never compare against ref-table `name`
  strings. (Neither finding touches financial math; the rule is
  cited for completeness.)
- DRY / SOLID / fully normalized schema.
- Type-hinted, substantive docstrings, specific exceptions, no
  Unicode em/en dashes.
- Never modify a test to make it pass. Both commits here are
  pure refactors; if any test fails the code is wrong.

---

## 1. Hard rules for executing this plan

Inherits every rule from
`docs/audits/financial_calculations/remediation_follow_up_common.md`
("Apply these rules (every commit)") verbatim; the floor is not the
ceiling. The additions that follow are specific to this plan:

1. **Run commits in order.** Section 6 records dependencies; F-25
   lands first because it is a single-package restructure with zero
   test edits, then F-24 because it is a wider multi-file extraction
   that benefits from a clean tree to diff against.
2. **Re-grep cited lines first.** Numbers below are accurate at
   authoring time (2026-05-21, head `a0782f7`). They will drift as
   the chain lands -- re-grep before editing.
3. **Refactors must be byte-equivalent at the wire level.** Neither
   commit changes route behaviour; the acceptance criterion is "every
   existing template + transfer-template CRUD test passes unchanged"
   plus "pylint warnings F-24 / F-25 cite are gone." A test that
   fails after either commit indicates the refactor introduced a
   semantic drift, not a stale assertion.
4. **Targeted pytest during edits; pylint `app/ --fail-on=E,F` clean;
   full pytest as the per-commit final gate** -- via
   `./scripts/test.sh`. Pylint must show **no new R0401 / R0801
   warnings** vs the post-gate baseline, and the specific warnings
   each commit targets must be gone.
5. **No migrations.** Neither commit touches the schema.
6. **No new packages.** Every helper extracted here reuses existing
   Flask / SQLAlchemy / stdlib primitives.
7. **Stay in scope.** Out-of-scope items surfaced during execution
   land in `remediation_follow_up.md` as a new F-N entry, never
   inline. The F-24 scope addition (Section 3) is the only such item
   the plan absorbs at authoring time.
8. **Do not push.** After every commit lands locally and the full
   suite is green, present results and ask before pushing to `dev`
   (CI runs; PR-to-`main` is required for promotion per
   `CLAUDE.md` Git Workflow).

---

## 2. Design decisions made for this plan

Captured at plan time so the developer can audit them before
execution.

| Decision | Choice | Rationale |
|---|---|---|
| **F-25 fix shape** | Option B (move `accounts_bp` declaration to a dedicated `app/routes/accounts/_bp.py` module) | The cycle is real (`app/routes/accounts/__init__.py` imports each submodule for the side effect of registering decorators; each submodule imports `accounts_bp` from the package init). Moving the blueprint declaration to a leaf module breaks the package -> submodule -> package round-trip pylint flags. Mechanical refactor across five files. Option A (accept the warning) is rejected because the developer opted to land the refactor at plan-presentation time. |
| **F-24 helper location** | New `app/routes/_recurrence_form_helpers.py` (route-layer, not service) | `handle_stale_conflict` needs Flask `flash`, `redirect`, `url_for` -- co-locating it in a service module would violate the project's "services are isolated from Flask" boundary (`CLAUDE.md::Architecture`). `build_recurrence_rule_from_form` is pure-data but co-locates naturally with its sibling. Leading underscore marks the module as route-internal, consistent with `app/routes/_route_audit.py` and similar internal helpers in the project. |
| **F-24 scope** | Helpers close every R0801 pair currently flagged across `app/routes/templates.py` and `app/routes/transfers.py`, not only the three pairs the F-24 write-up names | Verification surfaced 9 distinct R0801 matches between the two files. Six of them are variations of the `try: db.session.commit() except StaleDataError: rollback + log + flash + redirect` shape across archive / unarchive / hard-delete routes. Leaving them in place would mean the second helper (`handle_stale_conflict`) gets defined for two callers and used by only one of them -- a half-finished extraction the common rules explicitly forbid ("No half-finished implementations either"). |
| **`due_day_of_month` asymmetry** | Helper accepts an `include_due_day_of_month: bool` keyword, defaulting `False` | The `due_day_of_month` field is populated only from `TransactionTemplate` create/update schemas; the transfer-template schemas do not have it. The original duplication carried this asymmetry implicitly (transfers.py simply did not pop the key). The keyword makes the asymmetry explicit and self-documenting at every call site. |
| **F-24 helper return shape** | `build_recurrence_rule_from_form` returns `RecurrenceRule \| None`; `handle_stale_conflict` returns a Flask `Response` (the redirect) | Returning the response keeps the route's control flow identical to the pre-extraction shape (the route does `return handle_stale_conflict(...)`); the developer reading the route sees the same redirect-on-error pattern. An exception-based design (`raise StaleConflictRedirect(...)`) was considered and rejected as control-flow obfuscation for a one-line caller. |

---

## 3. Scope additions surfaced during verification

Re-verifying each finding against the live code uncovered one scope
addition; folded into the plan rather than logged as a new F-N entry
because it is the same DRY violation F-24 already names.

- **R-FU-3 (F-24 surface is wider than the entry documents).** The
  F-24 write-up names three R0801 pairs:
  - Recurrence-rule construction in *create* paths
  - Recurrence-rule construction in *update* paths
  - `RecurrenceConflict` / `StaleDataError` flash handlers
  Re-running `pylint --disable=all --enable=R0801 app/routes/transfers.py
  app/routes/templates.py` against `dev` (head `a0782f7`) surfaces
  **nine** distinct R0801 matches. The three F-24-named pairs are
  present; the other six are variations of the
  `try: commit() except StaleDataError: rollback + log + flash +
  redirect` shape in archive (`archive_template` /
  `archive_transfer_template`), unarchive (`unarchive_template` /
  `unarchive_transfer_template`), and hard-delete
  (`hard_delete_template` / `hard_delete_transfer_template`) routes.
  All nine close cleanly with `handle_stale_conflict`. Commit 2
  closes them as a single sweep so the helper does not get extracted
  and left half-applied.

---

## 4. Verification status table

Result of re-grepping every claim in the F-24 and F-25 entries
against the current `dev` branch.

| Item | Claim re-verified | Drift found? | Maps to commit |
|---|---|---|---|
| F-24 -- "three duplicated blocks" between templates.py and transfers.py | YES (named pairs all confirmed) | Wider surface: nine R0801 matches, not three (see Section 3) | C2 |
| F-24 -- "no behavioural change required" | YES (every difference is identifier-level: redirect URL, log message, schema field set) | None | C2 |
| F-24 -- "RecurrenceRule difference is `due_day_of_month` only" | YES (transfer schemas omit the field; templates schemas include it) | None | C2 |
| F-25 -- "four R0401 cyclic-import warnings rooted at `app/utils/account_validation.py:1`" | YES (`app.routes.accounts -> {crud, detail, anchor, types}` each cited once) | None | C1 |
| F-25 -- "package -> submodule -> package round-trip is the cycle" | YES (`__init__.py:54-57` imports submodules; each submodule imports `accounts_bp` from the package) | None | C1 |
| F-25 -- "`accounts_bp` has no `url_prefix` for behavioural reasons" | YES (decorators carry `/accounts` verbatim; preserving that is an F-1 acceptance criterion) | None (constraint must be preserved through the refactor) | C1 |

---

## 5. Commit checklist

Two implementation commits plus a final gate. Each row is one git
commit; messages use `<type>(<scope>): <what>` per Definition of
Done.

| # | Commit message | Closes |
|---|---|---|
| 1 | `refactor(routes): move accounts_bp to dedicated module to break import cycle (F-25)` | F-25 |
| 2 | `refactor(routes): extract recurrence-rule and stale-conflict helpers for templates/transfers (F-24)` | F-24 |
| 3 | `chore(release): F-24/F-25 follow-up final gate` | -- |

---

## 6. Commit dependency analysis

```text
Independent (no cross-commit dependencies):
  1 F-25 accounts package restructure
  2 F-24 templates/transfers helper extraction

Final gate:
  3 full suite + pylint + R0401 / R0801 verification
```

Ordering rationale: F-25 first because it is a single-package
mechanical refactor with no test edits and no behavioural surface --
a clean warm-up. F-24 second because it is the multi-file extraction
with new helpers and the new helper-unit tests. The final gate runs
both verification suites together. F-25 and F-24 do not share any
files, so they could be re-ordered without conflicts; the chosen
order is reviewer-attention optimisation, not technical necessity.

---

## 7. Commits (detailed)

Each commit follows: A message, B problem, C files, D implementation,
E tests, F manual verification, G downstream, H rollback.

---

### Commit 1 -- Move `accounts_bp` to dedicated module to break import cycle (F-25)

**A. Commit message** `refactor(routes): move accounts_bp to dedicated module to break import cycle (F-25)`

**B. Problem statement** Pylint reports four `R0401 Cyclic import`
warnings rooted at `app/utils/account_validation.py:1`:

```
R0401: Cyclic import (app.routes.accounts -> app.routes.accounts.crud)
R0401: Cyclic import (app.routes.accounts -> app.routes.accounts.detail)
R0401: Cyclic import (app.routes.accounts -> app.routes.accounts.anchor)
R0401: Cyclic import (app.routes.accounts -> app.routes.accounts.types)
```

The cycle flows from the Commit-21 (F-1) blueprint split:
`app/routes/accounts/__init__.py:54-57` imports each submodule for
the side effect of registering its route decorators; each submodule
(`crud.py:42`, `anchor.py:36`, `types.py:26`, `detail.py:35`) imports
`accounts_bp` from `app.routes.accounts`, which pylint traces back to
the package init and reports as cyclic. The cycle is real but benign
at runtime because submodule reads of `accounts_bp` happen during
their module body execution -- by the time
`from app.routes.accounts import crud` returns, `accounts_bp` is
already bound in the package namespace.

Score impact today is zero (`9.57/10 (previous run: 9.57/10, +0.00)`
at the F-24/F-25 gate authoring time); the warning is a refactor
hint, not an error. The fix removes the warning so future cyclic
imports surface as new offenders, not lost in the noise of four
known-accepted ones.

**C. Files modified**

- `app/routes/accounts/_bp.py` (new) -- one-line module that declares
  `accounts_bp` and carries the rationale docstring currently in
  `__init__.py:38-46`.
- `app/routes/accounts/__init__.py` -- replaces the `accounts_bp =
  Blueprint(...)` declaration with `from app.routes.accounts._bp
  import accounts_bp` (re-export so existing
  `from app.routes.accounts import accounts_bp` consumers stay
  valid); preserves the side-effect submodule imports.
- `app/routes/accounts/crud.py` -- imports
  `accounts_bp` from `app.routes.accounts._bp` instead of the package.
- `app/routes/accounts/anchor.py` -- same.
- `app/routes/accounts/types.py` -- same.
- `app/routes/accounts/detail.py` -- same.

**D. Implementation approach**

1. Read `app/routes/accounts/__init__.py` end-to-end (57 lines) to
   confirm the current shape and capture the "no `url_prefix`"
   rationale docstring.
2. Read each submodule's import block (`crud.py:1-50`,
   `anchor.py:1-40`, `types.py:1-30`, `detail.py:1-40`) to confirm
   each one imports `accounts_bp` from `app.routes.accounts`. None
   of the submodules should import sibling submodules; if any do,
   the cycle is wider than F-25 documents and the plan needs
   revision before continuing.
3. Create `app/routes/accounts/_bp.py`:
   ```python
   """
   Shekel Budget App -- Accounts Package Blueprint Declaration

   Holds the ``accounts_bp`` ``Blueprint`` instance in a leaf module
   so the per-sub-domain modules can import it without going back
   through ``app.routes.accounts.__init__``.  Pre-F-25 the blueprint
   lived in the package init, which meant the package <-> submodule
   import round-trip surfaced as four pylint ``R0401 Cyclic import``
   warnings rooted at ``app/utils/account_validation.py:1``.

   No ``url_prefix`` is set: every route decorator in the sibling
   modules carries the ``/accounts`` prefix explicitly (preserved
   verbatim from the pre-split monolith).  Adding ``url_prefix``
   here would require stripping every decorator's prefix in
   lockstep -- a behavioural change the F-1 acceptance criteria
   explicitly forbids.
   """
   from flask import Blueprint

   accounts_bp = Blueprint("accounts", __name__)
   ```
4. Rewrite `app/routes/accounts/__init__.py`:
   - Keep the module docstring (it documents the package layout for
     readers).
   - Replace the `accounts_bp = Blueprint(...)` declaration with
     `from app.routes.accounts._bp import accounts_bp` and add a
     comment explaining why the declaration moved.
   - Preserve the `from app.routes.accounts import crud` ... side-
     effect imports verbatim (their semantics are unchanged; only
     the source of `accounts_bp` has moved).
   - Optionally add `__all__ = ["accounts_bp"]` to make the re-
     export explicit (and silence any future linter that flags the
     `noqa: F401` re-export).
5. In each submodule (`crud.py`, `anchor.py`, `types.py`,
   `detail.py`), change the import:
   ```python
   from app.routes.accounts import accounts_bp
   ```
   to:
   ```python
   from app.routes.accounts._bp import accounts_bp
   ```
   No other change to any submodule -- the route decorators
   continue to bind against the same blueprint instance.
6. Verify `from app.routes.accounts import accounts_bp` still
   resolves (used by `app/__init__.py:443`); the re-export in
   `__init__.py` preserves that path.

**E. Test cases**

- C1-1 every existing test in `tests/test_routes/test_accounts.py`
  passes unchanged (137 tests; the URL surface and route behaviour
  are byte-equivalent).
- C1-2 add a one-line unit test in
  `tests/test_routes/test_accounts.py` (or `test_app_factory.py` if
  one exists) asserting
  `app.routes.accounts.accounts_bp is app.routes.accounts._bp.accounts_bp`
  -- pins the re-export contract so a future cleanup that drops the
  re-export would fail loud.
- C1-3 `pylint --disable=all --enable=R0401 app/` shows zero matches
  rooted in `app/routes/accounts/` or
  `app/utils/account_validation.py`.

**F. Manual verification steps**

1. `grep -rn "from app.routes.accounts import accounts_bp" /home/josh/projects/Shekel/`
   returns matches only at the top of `app/__init__.py` (the
   factory-time blueprint registration) and any test fixture that
   imports the symbol -- never from a sibling submodule.
2. `grep -rn "from app.routes.accounts._bp import accounts_bp" /home/josh/projects/Shekel/`
   returns matches in all four submodules and the package init
   (the re-export source).
3. `python -c "from app.routes.accounts import accounts_bp; print(accounts_bp.name)"`
   prints `accounts`.
4. Start the dev server; visit `/accounts`, click into a checking
   account, edit an anchor, edit an account, create / delete an
   account type. All five sub-domain flows route correctly.

**G. Downstream effects** None at the user surface; URLs and
behaviours are byte-equivalent. The pylint R0401 noise is gone, so
future genuine cyclic-import regressions in the project will surface
as new offenders rather than getting lost in the four known-accepted
warnings.

**H. Rollback notes** `git revert`. The split is a pure
re-organisation; the package init can be restored verbatim from
history.

---

### Commit 2 -- Extract recurrence-rule and stale-conflict helpers for templates/transfers (F-24)

**A. Commit message** `refactor(routes): extract recurrence-rule and stale-conflict helpers for templates/transfers (F-24)`

**B. Problem statement** `pylint --disable=all --enable=R0801
app/routes/templates.py app/routes/transfers.py` reports nine
distinct R0801 (similar-lines) duplicates between the two CRUD
modules:

- Two `recurrence_pattern` + `RecurrenceRule(...)` blocks in the
  create / update paths (`templates.py:191-201` vs
  `transfers.py:169-179`; `templates.py:347-352` vs
  `transfers.py:357-362`).
- One `recurrence_pattern is None` short-circuit pair
  (`templates.py:341-346` vs `transfers.py:174-179`,
  `templates.py:196-201` vs `transfers.py:351-356`).
- Six variations of the
  `try: db.session.commit() except StaleDataError: rollback +
  logger.info + flash + redirect` shape across the create, update,
  archive, unarchive, and hard-delete paths
  (`templates.py:[437-442, 484-489, 537-542, 607-613, 638-644]` vs
  `transfers.py:[442-447, 507-512, 574-579, 662-668, 710-716]`).

Each pair has one route-specific axis -- the `due_day_of_month` field
exists only on transaction-template schemas, the redirect endpoints
differ per route, and the log messages name the route -- so a literal
extraction is not a one-liner. The shape is otherwise byte-equivalent
across both modules. The duplication is stylistic, not behavioural,
but it sits at the largest contiguous shared surface in
`app/routes/` and consolidating it both removes the R0801 noise and
centralises two project-wide error-handling contracts (stale-conflict
flash language, recurrence-rule construction).

**C. Files modified**

- `app/routes/_recurrence_form_helpers.py` (new) -- two helpers:
  - `build_recurrence_rule_from_form(data, user_id, *,
    include_due_day_of_month) -> RecurrenceRule | None`
  - `handle_stale_conflict(*, logger, route_log_id, edit_endpoint,
    edit_kwargs, flash_message) -> Response`
- `app/routes/templates.py` -- call the helpers in
  `create_template`, `update_template`, `archive_template`,
  `unarchive_template`, `hard_delete_template`.
- `app/routes/transfers.py` -- same in
  `create_transfer_template`, `update_transfer_template`,
  `archive_transfer_template`, `unarchive_transfer_template`,
  `hard_delete_transfer_template`. Each transfer-side call passes
  `include_due_day_of_month=False`.
- `tests/test_routes/test_recurrence_form_helpers.py` (new) --
  unit tests for both helpers covering the no-pattern path, the
  every-N-periods auto-offset path, the `due_day_of_month` axis,
  and the stale-conflict redirect shape.

**D. Implementation approach**

1. Read each cited block in `app/routes/templates.py` and
   `app/routes/transfers.py` in full to confirm the bodies match
   the pylint output (every line is what the helper will replace).
2. Read `app/models/recurrence_rule.RecurrenceRule.__init__` to
   confirm the kwargs the helper passes.
3. Create `app/routes/_recurrence_form_helpers.py`:
   ```python
   """
   Shekel Budget App -- Recurrence-Form Route Helpers

   Two helpers shared between the transaction-template
   (:mod:`app.routes.templates`) and transfer-template
   (:mod:`app.routes.transfers`) CRUD routes:

   * :func:`build_recurrence_rule_from_form` -- consumes a
     Marshmallow-validated payload, pops the recurrence-related
     keys, and returns a fresh :class:`RecurrenceRule` or ``None``.
   * :func:`handle_stale_conflict` -- emits the canonical
     stale-conflict flash + redirect when a commit raises
     :class:`StaleDataError`.

   Route-layer module rather than service because
   :func:`handle_stale_conflict` consumes Flask ``flash`` /
   ``redirect`` / ``url_for``; ``CLAUDE.md::Architecture`` keeps
   services isolated from Flask globals.  The leading underscore
   marks the module as route-internal.
   """
   import logging
   from typing import Any

   from flask import flash, redirect, url_for
   from flask.wrappers import Response
   from sqlalchemy.exc import IntegrityError
   from sqlalchemy.orm.exc import StaleDataError

   from app import ref_cache
   from app.enums import RecurrencePatternEnum
   from app.extensions import db
   from app.models.pay_period import PayPeriod
   from app.models.recurrence_pattern import RecurrencePattern
   from app.models.recurrence_rule import RecurrenceRule


   _STALE_CONFLICT_FLASH = (
       "This {noun} was changed by another action while you were "
       "editing.  Please reload and try again."
   )


   def build_recurrence_rule_from_form(
       data: dict[str, Any],
       *,
       user_id: int,
       start_period_id: int | None,
       end_date,  # date | None -- date import omitted for brevity
       redirect_endpoint: str,
       redirect_endpoint_kwargs: dict[str, Any] | None = None,
       include_due_day_of_month: bool = False,
   ) -> RecurrenceRule | Response | None:
       """Construct a :class:`RecurrenceRule` from validated form data.

       Pops every recurrence-related key from ``data`` so the
       caller's downstream model constructor does not receive them
       as stray kwargs.  Returns the new rule (added to the session
       and flushed), ``None`` when no pattern was selected (every
       recurrence key is still popped), or a Flask redirect
       :class:`Response` when validation fails (invalid pattern id,
       invalid start period for every-N-periods auto-offset).

       Args:
           data: Marshmallow-validated payload; mutated in place.
           user_id: Owner of the resulting rule.
           start_period_id: From the form; needed for the
               every-N-periods auto-offset derivation.
           end_date: From the form; copied verbatim into the rule.
           redirect_endpoint: Flask endpoint name for the
               redirect-on-error response.
           redirect_endpoint_kwargs: Extra kwargs for ``url_for``.
           include_due_day_of_month: ``True`` for transaction
               templates, ``False`` for transfer templates.  The
               transfer-template schemas do not expose
               ``due_day_of_month``; passing ``True`` for a
               transfer payload would raise ``KeyError`` on the
               ``data.pop`` of an absent key, defeating the
               schema-side contract.
       """
       redirect_endpoint_kwargs = redirect_endpoint_kwargs or {}
       pattern_id_str = data.pop("recurrence_pattern", None)
       if not pattern_id_str:
           # No pattern: drop every recurrence-related key so the
           # caller's TransactionTemplate / TransferTemplate
           # constructor does not receive stray kwargs.
           recurrence_keys = (
               "interval_n", "offset_periods", "day_of_month",
               "month_of_year", "end_date",
           )
           if include_due_day_of_month:
               recurrence_keys = recurrence_keys + ("due_day_of_month",)
           for key in recurrence_keys:
               data.pop(key, None)
           return None

       pattern = db.session.get(RecurrencePattern, int(pattern_id_str))
       if pattern is None:
           flash("Invalid recurrence pattern.", "danger")
           return redirect(url_for(
               redirect_endpoint, **redirect_endpoint_kwargs,
           ))

       interval_n = data.pop("interval_n", 1)
       offset_periods = data.pop("offset_periods", 0)

       # Auto-derive offset from start period for every_n_periods.
       every_n_id = ref_cache.recurrence_pattern_id(
           RecurrencePatternEnum.EVERY_N_PERIODS,
       )
       if (int(pattern_id_str) == every_n_id
               and start_period_id and interval_n):
           start_period = db.session.get(PayPeriod, start_period_id)
           if not start_period or start_period.user_id != user_id:
               flash("Invalid start period.", "danger")
               return redirect(url_for(
                   redirect_endpoint, **redirect_endpoint_kwargs,
               ))
           offset_periods = start_period.period_index % interval_n

       rule_kwargs = dict(
           user_id=user_id,
           pattern_id=pattern.id,
           interval_n=interval_n,
           offset_periods=offset_periods,
           day_of_month=data.pop("day_of_month", None),
           month_of_year=data.pop("month_of_year", None),
           start_period_id=start_period_id,
           end_date=end_date,
       )
       if include_due_day_of_month:
           rule_kwargs["due_day_of_month"] = data.pop(
               "due_day_of_month", None,
           )

       rule = RecurrenceRule(**rule_kwargs)
       db.session.add(rule)
       db.session.flush()
       return rule


   def handle_stale_conflict(
       *,
       logger: logging.Logger,
       route_log_id: int,
       route_log_label: str,
       edit_endpoint: str,
       edit_endpoint_kwargs: dict[str, Any],
       noun: str,
   ) -> Response:
       """Roll back, log, flash, and redirect for stale-data conflicts.

       The canonical handler for the
       ``try: db.session.commit() except StaleDataError`` pattern
       that appears across every templates / transfers mutation
       route.  Called from inside the ``except`` block -- the
       caller is responsible for the ``try`` and for re-raising
       any other exception.

       Args:
           logger: Per-module logger; the helper does not own one.
           route_log_id: The mutating template / transfer id, used
               in the log message.
           route_log_label: Short label for the log message, e.g.
               ``"update_template"`` or
               ``"hard_delete_transfer_template"``.
           edit_endpoint: Flask endpoint to redirect the user to.
           edit_endpoint_kwargs: Kwargs for ``url_for``.
           noun: Human-readable noun for the flash message,
               ``"recurring transaction"`` or
               ``"recurring transfer"``.

       Returns:
           A Flask redirect :class:`Response`.  The caller returns
           it directly so the route's control flow is identical to
           the pre-extraction shape.
       """
       db.session.rollback()
       logger.info(
           "Stale-data conflict on %s id=%d",
           route_log_label, route_log_id,
       )
       flash(_STALE_CONFLICT_FLASH.format(noun=noun), "warning")
       return redirect(url_for(edit_endpoint, **edit_endpoint_kwargs))
   ```
4. Refactor `app/routes/templates.py::create_template`:
   ```python
   from app.routes._recurrence_form_helpers import (
       build_recurrence_rule_from_form,
       handle_stale_conflict,
   )
   # ... inside the route, replacing the existing block:
   rule_or_redirect = build_recurrence_rule_from_form(
       data,
       user_id=current_user.id,
       start_period_id=start_period_id,
       end_date=end_date,
       redirect_endpoint="templates.new_template",
       include_due_day_of_month=True,
   )
   if isinstance(rule_or_redirect, Response):
       return rule_or_redirect
   rule = rule_or_redirect  # RecurrenceRule | None
   ```
5. Repeat for `update_template` (pass
   `redirect_endpoint="templates.edit_template"`,
   `redirect_endpoint_kwargs={"template_id": template_id}`).
6. Replace each `try: db.session.commit() except StaleDataError:`
   block with:
   ```python
   try:
       db.session.commit()
   except StaleDataError:
       return handle_stale_conflict(
           logger=logger,
           route_log_id=template.id,
           route_log_label="update_template",
           edit_endpoint="templates.edit_template",
           edit_endpoint_kwargs={"template_id": template_id},
           noun="recurring transaction",
       )
   ```
   Preserve any sibling `except IntegrityError:` branches unchanged
   -- they have route-specific bodies that do not generalise.
7. Mirror in `app/routes/transfers.py` with
   `include_due_day_of_month=False`,
   `noun="recurring transfer"`, and the matching endpoint names.
8. The update-path's existing-rule mutation branch
   (`if template.recurrence_rule: template.recurrence_rule.pattern_id =
   pattern.id; ...`) does NOT extract cleanly -- it mutates an
   existing rule rather than constructing a new one. Leave that
   branch in place; only the `else: rule = RecurrenceRule(...)`
   branch routes through the helper (matches the helper's "construct
   fresh rule" contract). The R0801 match on the mutation branch
   stays; it is acceptable because the only alternative (a second
   helper for the mutation path) would amount to "set every field
   from `data.pop` calls" which adds no abstraction value over the
   inline form.
9. Pylint after each route edit to confirm the targeted R0801
   warnings are gone and no new R0801 / R0401 has appeared.

**E. Test cases**

The helper unit tests live in
`tests/test_routes/test_recurrence_form_helpers.py` (new file). The
existing CRUD route tests cover the helper through its integration
surface; the new file pins helper-internal contracts.

- C2-1 `build_recurrence_rule_from_form` with `recurrence_pattern=None`
  returns `None` and pops every recurrence key from the data dict
  (including `due_day_of_month` when `include_due_day_of_month=True`).
- C2-2 with an invalid `recurrence_pattern` id, returns a Flask
  redirect `Response` (status 302) targeting the `redirect_endpoint`
  and flashes "Invalid recurrence pattern."
- C2-3 with `pattern_id_str = EVERY_N_PERIODS` and a valid
  `start_period`, derives `offset_periods = start_period.period_index
  % interval_n` and constructs the rule. Hand-arithmetic for
  `period_index=37`, `interval_n=4`: `37 % 4 = 1`.
- C2-4 with `pattern_id_str = EVERY_N_PERIODS` and an invalid
  `start_period_id` (does not exist), returns a Flask redirect
  `Response` and flashes "Invalid start period."
- C2-5 with `include_due_day_of_month=True` and
  `due_day_of_month=15` in the data, the constructed rule has
  `due_day_of_month=15` and the data dict no longer contains the
  key. With `include_due_day_of_month=False`, the constructed rule
  has no `due_day_of_month` attribute set (the constructor default
  applies) and the data dict is not probed for the key.
- C2-6 `handle_stale_conflict` rolls back the session, logs at
  INFO with the label and id in the message, flashes the canonical
  string with the noun substituted, and returns a 302 redirect to
  the `edit_endpoint`.
- C2-7 the existing templates / transfers CRUD route tests pass
  unchanged (every route's wire-level behaviour is identical).
- C2-8 `pylint --disable=all --enable=R0801 app/routes/templates.py
  app/routes/transfers.py` shows no matches for any of the nine
  pre-extraction pairs. (Some R0801 matches may remain on the
  existing-rule mutation branch -- the design decision in Section
  2 explicitly accepts those.)

**F. Manual verification steps**

1. `grep -nF "build_recurrence_rule_from_form" app/routes/` returns
   matches in `templates.py`, `transfers.py`, and the helper module.
2. `grep -nF "handle_stale_conflict" app/routes/` returns matches in
   `templates.py`, `transfers.py`, and the helper module; ten call
   sites total (five per file, one per mutation route).
3. `grep -nF "_STALE_CONFLICT_FLASH" app/` returns one match (the
   helper module).
4. `grep -nE "except StaleDataError:" app/routes/templates.py
   app/routes/transfers.py` returns five matches per file -- one per
   mutation route -- each followed by the `return
   handle_stale_conflict(...)` call.
5. Start the dev server. Create a recurring transaction template
   with the EVERY_N_PERIODS pattern; assert the rule's offset is
   `period_index % interval_n`. Open the same template in two
   browser tabs, edit and save the first, then save the second --
   the second should display the canonical stale-conflict flash and
   redirect to the edit form. Repeat for transfer templates.
6. Full test suite green.

**G. Downstream effects** Future templates / transfers CRUD edits
inherit two well-tested helpers; new routes that need either contract
do not re-implement the body. The stale-conflict flash language is
now centralised, so a future copy edit lives in one place.

**H. Rollback notes** `git revert`. Both helpers are additive; the
inlined bodies can be restored verbatim from history per route. If
only one helper proves problematic, the per-route revert is
mechanical (the helper module stays, the affected route returns to
its pre-extraction body).

---

### Commit 3 -- F-24 / F-25 follow-up final gate

**A. Commit message** `chore(release): F-24/F-25 follow-up final gate`

**B. Problem statement** Acceptance gate for the F-24 / F-25
follow-up. Confirms both commits landed cleanly, the full suite is
green, pylint is clean and the targeted R0401 / R0801 warnings are
gone, and the documented status of both findings is updated. No
code changes.

**C. Files modified**

- `docs/audits/financial_calculations/remediation_follow_up.md` --
  update F-24 and F-25 `**Status:**` lines from
  "**Status:** not started" to
  "**Status:** resolved by Commit N of
  `remediation_follow_up_F24_F25_plan.md`" (one line per item).

**D. Implementation approach (gate checklist -- all must pass before this commit)**

Per `remediation_follow_up_common.md` "Apply these rules (every
commit)" plus the additions in this plan's Section 1:

1. `python scripts/build_test_template.py` -- NOT required (no
   migration in this plan), but run it anyway if `app/ref_seeds.py`
   or `app/audit_infrastructure.py` was touched. Confirm by
   `git log --name-only origin/dev..HEAD` showing neither file.
2. `./scripts/test.sh` -- ends in `N passed`, zero
   failed/errors/xfailed. Capture the final summary line and
   include it in the commit body and section E of the work summary.
3. `pylint app/ --fail-on=E,F` -- clean, no new warnings vs the
   post-Commit-22 (head `a0782f7`) baseline (`9.57/10`). The
   targeted R0401 and R0801 warnings are gone:
   - `pylint --disable=all --enable=R0401 app/` shows no matches
     rooted in `app/routes/accounts/` or
     `app/utils/account_validation.py`.
   - `pylint --disable=all --enable=R0801 app/routes/templates.py
     app/routes/transfers.py` shows no matches for any of the nine
     pre-extraction pairs (the existing-rule mutation branch may
     still show; that is acceptable per Section 2).
4. No migrations -- step skipped.
5. The existing static guards from prior chains (cross-page balance
   lock, ARM-window stability lock, F-6 grid/accounts guards, C8-5
   static guard) all green. The C8-5 guard is the most relevant
   here because Commit 1 of this plan touches the accounts
   package; verify
   `tests/test_services/test_year_end_summary_service.py::test_no_external_calculate_balances_callers`
   still passes after the package restructure.
6. Sweep `remediation_follow_up.md`: change F-24's and F-25's
   `**Status:**` lines from "not started" to "resolved by Commit N
   of `remediation_follow_up_F24_F25_plan.md`."
7. `git status` shows only the docs file changed (this commit is
   gate + bookkeeping).

**E. Test cases** The entire suite is the test case. Acceptance:
full green suite, clean pylint with the targeted warnings gone, the
documented status of F-24 and F-25 updated.

**F. Manual verification steps**

1. Walk the affected route surfaces in the dev server:
   - Visit `/accounts`, click into a checking account, edit an
     anchor, edit an account, create / delete an account type
     (F-25 acceptance).
   - Create a recurring transaction template; edit it; archive /
     unarchive / hard-delete it. Same flow for a transfer template
     (F-24 acceptance, every helper call site exercised).
2. Confirm `remediation_follow_up.md` shows F-24 and F-25 as
   resolved.

**G. Downstream effects** Both follow-up entries are closed. The
project's pylint output no longer carries the four R0401 cyclic-
import warnings or the nine R0801 templates/transfers duplicates;
genuine future regressions in either area surface as new offenders
rather than as additions to known-accepted noise.

**H. Rollback notes** No code change in this commit; revert only
the docs file if any step in the gate fails on rebuild.

---

## 8. End-to-end verification (no user-visible symptoms)

Neither F-24 nor F-25 fixes a user-visible defect. Both close pylint
warnings that the main remediation chain accepted as known-tolerated
at the time. The verification surface is therefore:

1. **Pylint targeted warnings gone.** The four R0401 in
   `accounts/` are no longer reported; the nine R0801 between
   templates.py and transfers.py for the three F-24-named pairs
   plus the six wider stale-conflict variations are no longer
   reported.
2. **Pylint score baseline preserved or improved.** The post-Commit-
   22 baseline is `9.57/10`. Either commit may bump the score
   marginally; neither should drop it.
3. **Behaviour unchanged.** Every existing route test
   (`tests/test_routes/test_accounts.py`,
   `tests/test_routes/test_templates.py`,
   `tests/test_routes/test_transfers.py`) passes without
   modification; this is the load-bearing assertion that the
   refactor is byte-equivalent at the wire level.
4. **New helper-unit tests pass.** Commit 2's
   `tests/test_routes/test_recurrence_form_helpers.py` pins both
   helpers' internal contracts so future edits to either helper
   surface failures at the unit-test layer rather than as
   integration drift.

---

## 9. Out-of-scope items flagged during planning

Discovered during verification or while drafting commits; deliberately
NOT included in this plan. The developer chooses whether to promote
any of them later.

- **R0801 on the update-path existing-rule mutation branch.**
  Commit 2's helper covers the "construct fresh rule" branch only;
  the "mutate existing rule" branch (`if template.recurrence_rule:
  template.recurrence_rule.pattern_id = pattern.id; ...`) still
  matches as R0801 between templates.py and transfers.py. Extracting
  it would replace nine lines of `setattr`-style assignments with a
  helper whose body is identical to the inline form -- no
  abstraction value, just one more indirection. Acceptable as-is;
  Section 2 explicitly records the choice.

- **Other R0801 across `app/routes/`.** A whole-repo R0801 run may
  surface duplicates outside templates/transfers (e.g. the FK-
  ownership check loops in transfers.py and the accounts package).
  Out of scope for F-24, which names templates/transfers specifically.
  If a future audit finds equivalent duplications elsewhere, they
  warrant their own follow-up entry.

- **`accounts_bp` rename or splitting into per-sub-domain
  blueprints.** Commit 21 (F-1) explicitly locked Option A
  (single blueprint, file-split). F-25 inherits that constraint;
  going to per-sub-domain blueprints would require updating every
  `url_for("accounts.X")` call site and is a separate design
  question.

- **Service-layer home for `build_recurrence_rule_from_form`.** The
  helper is pure-data (could live in `app/services/recurrence_engine.py`),
  but its sibling `handle_stale_conflict` is Flask-tied. Co-locating
  both in a route-layer helper module is the pragmatic call. If a
  service-layer caller appears for the data-only helper, moving it
  to a service module is mechanical.

These are **suggestions**, not commitments. Mention to the developer
at plan-presentation time and let them choose to promote any to a
follow-up commit.

---

## 10. Notes on executing this plan

- Run commits in order; Section 6's order is reviewer-attention
  optimisation, not technical necessity, but landing F-25 first
  keeps the F-24 diff focused on the helper extraction without
  noise from the accounts-package restructure.
- Every commit: re-grep cited lines first, targeted tests during,
  `pylint app/ --fail-on=E,F` plus the targeted R0401 / R0801 runs,
  then the full suite as the per-commit final gate and the plan-
  final gate (Commit 3).
- Neither commit modifies tests to make them pass. If any existing
  test fails after either commit, the refactor introduced a
  semantic drift -- the code is wrong, not the test.
- No migrations; no destructive changes; both commits are pure
  refactors with additive helper modules.
- This plan is a remediation plan only. No code is changed by
  producing it. Execution happens in separate sessions, one commit
  (or small group) per session, suite green before moving on.
- After Commit 3 lands, both F-24 and F-25 are closed and
  `remediation_follow_up.md` carries no "not started" entries
  (the existing F-N entries that were closed by the first follow-up
  plan stay marked resolved; the new F-25 entry will be marked
  resolved by this plan's Commit 1).
