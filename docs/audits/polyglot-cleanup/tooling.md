# Linter Tooling Comparison and Decisions -- 2026-06-12

Companion to `findings.md`. Web-verified comparison (7 research agents,
mid-2026 sources, several candidates benchmarked against this actual
repo) of linter candidates per surface, under the project constraints:
no Node runtime, pacman-first installs, per-edit-hook latency budget
(tens of ms), and floors lockable into the four enforcement layers
(per-edit hook, pre-commit, CI, hard floor) like pylint's 10.00/10.

Calibrating fact from the audit: only ~11 of 124 findings were
mechanically catchable at all. Tools below are adopted as REGRESSION
FLOORS for the future, not as the fix for the audited findings.

## JavaScript -- Biome (over oxlint, ESLint, quick-lint-js, deno lint)

- **Biome 2.3.15**: in official Arch repos (only candidate that is);
  single Rust binary, no Node; **measured on this repo: 36 ms
  single-file, 44 ms all 23 files** -- well inside hook budget. ~423 JS
  rules. GritQL plugin engine = native custom house rules (the analog
  of `tools/pylint/shekel_checkers.py`) without Node. Also lints CSS
  and JSON (consolidation).
- **oxlint 1.69**: best correctness-first default posture and weekly
  releases, but AUR-only on the host (manual update + trust burden for
  a locked gate), no formatter, and custom rules beyond esquery
  selectors require its npm-distributed plugins alpha (Node).
- **ESLint 10.x**: its genuinely unique value here is
  eslint-plugin-compat + eslint-plugin-no-unsanitized -- mapping to ~1
  of 124 audit findings. Requires Node + node_modules. Does not clear
  the bar; a Biome GritQL plugin can recover the highest-value
  no-unsanitized pattern (`innerHTML =` assignment) natively.
- **quick-lint-js**: no stable release since 2024-03; non-configurable.
  **deno lint**: wrong ecosystem (no browser-global ergonomics).

Adoption config (empirically grounded):
1. Disable `suspicious/noRedundantUseStrict` -- proven false positive:
   Biome parses .js as modules (no sourceType:script switch) and flags
   the meaningful `'use strict'` in IIFEs (anchor_edit.js,
   password_toggle.js).
2. Enable `correctness/noUndeclaredVariables` (off by default) with
   `javascript.globals = ["ShekelChart","Chart","htmx","bootstrap",
   "activePopover","closeFullEdit"]` -- the measured-complete allowlist
   (79 raw hits reduce to exactly these 6). Treat any growth of this
   list as a design smell (each entry is an undeclared cross-file
   global).
3. Formatter OFF at adoption; lint only. Baseline: 20 diagnostics, 18
   of them stylistic (useArrowFunction x15...) -- decide style rules
   deliberately, do not inherit them.
4. CI: version-pinned standalone biome-linux-x64 release binary.

## CSS -- Biome CSS side (over stylelint, over nothing)

- Biome's CSS floor: duplicate properties, descending specificity,
  missing `var()`, unknown property/function/unit/pseudo, hex literals
  (`nursery/noHexColors` mechanizes Steel Ink token discipline).
  Effectively free alongside the JS adoption.
- **stylelint**: the deeper CSS linter, but its real delta on this
  1.9k-line token-disciplined codebase is ~1.5 low-severity audit
  findings, and it requires Node -- rejected on capability-gap vs
  constraint.
- 9 of 12 audit CSS findings were judgment-only: the code-reviewer
  subagent + /standards remain the primary CSS defense.

Caveats: confirm `noHexColors` exists in the pacman build before
relying on it (nursery rules move between versions -- and the
host-pacman vs CI-pinned-binary split means nursery semantics can
drift; pin CI to the same minor version as host). Burn down existing
hex literals BEFORE locking. Leave `noImportantStyles` off (grid.css
uses deliberate, commented `!important`). CSS formatter off.

## Shell -- shellcheck + targeted optional checks; shfmt as companion

- **shellcheck 0.11 default profile catches ZERO of the audit's 3
  shell findings** (verified by running it on this repo). The
  masked-errexit class that actually bit this project needs BOTH
  optional groups: `enable=check-set-e-suppressed` (SC2310/SC2311) and
  `enable=check-extra-masked-returns` (SC2312) in `.shellcheckrc`.
- Measured baseline on this repo: 13 default findings (6 SC2086,
  5 SC1091, 2 SC2329) + 55 extended (39 SC2310, 16 SC2312); expect ~3
  real fixes and ~50 rationale-suffixed suppressions (the disable-with-
  rationale convention carries over from pylint).
- Also in `.shellcheckrc`: `source-path=SCRIPTDIR`,
  `external-sources=true` (clears the SC1091 unfollowed-source noise).
- No credible alternative exists in 2026 (bashate dormant since 2022;
  shellharden is a quoting-only companion; bash -n redundant).
- **shfmt 3.13** as formatter: use `-i 4 -ci -bn` (style-preserving for
  this codebase -- NOT the Google `-i 2` profile). ~2 ms per file.
- Measured hook cost: shellcheck ~40-90 ms + shfmt ~2 ms per file.

## Jinja templates -- djlint LINT-ONLY + free jinja2 parse check

- **djlint 1.39.2** (pip, version-pinned in requirements-dev.txt),
  `--profile=jinja`, lint mode ONLY. The reformatter is permanently
  out: djlint's own docs document rendering-semantics whitespace
  changes -- unreviewable risk across 13.4k HTMX-heavy lines.
- Measured baseline: 283 findings. Rule pruning decided at adoption:
  ignore H029 (83x, uppercase POST -- style), H023 (25x), H030/H031
  (meta tags -- auth-walled app), H006; decide T003 (135x unnamed
  `{% endblock %}` -- a one-time naming pass is genuinely useful in a
  13.4k-line tree, then keep the rule on). Keep H021 (inline styles --
  the audit's CSP-dead class), H037, T002, T028, H014.
- Pair with a tiny in-repo syntax check using the already-pinned jinja2
  (`create_app().jinja_env.parse()` per changed template) -- zero new
  dependencies, catches template syntax errors pre-render.
- Dead alternatives verified: curlylint (2022), jinjalint (2018);
  j2lint is Ansible-flavored with zero HTML awareness;
  prettier-plugin-jinja-template is formatter-only + Node.
- The audit's cross-partial duplicate-id class is invisible to EVERY
  source-level template linter (ids assemble at render time); the
  correct mechanism is a rendered-output pytest (duplicate-id scan over
  rendered pages) -- tracked as a fix-phase item, not a linter.

## Workflows/YAML -- actionlint + zizmor + thin yamllint

- **actionlint** (pacman): expression type-checking, runner labels,
  action inputs, and an embedded shellcheck pass over `run:` blocks --
  export `SHELLCHECK_OPTS` in the wrapper so run-blocks share the
  repo's optional-check profile (a repo .shellcheckrc may not reach
  actionlint's embedded invocation).
- **zizmor** (security auditor): catches BOTH workflow audit findings
  (WF-01 missing permissions, WF-02 tag-pinned actions); actionlint
  catches NEITHER. Near-zero overlap -- complements, not substitutes.
  GitHub's own gh-aw CLI shells out to exactly this pair. Run
  `--offline` in hooks; token-enabled in CI. Decide the pinning policy
  up front (its default demands commit-SHA pins for all actions --
  aligned with the audit's WF-02 remedy).
- **yamllint** (pacman): thin layer for compose/other YAML (syntax,
  key duplicates); configure for the heavily-commented compose style.
- **prettier rejected**: pacman package hard-depends on nodejs
  (verified) for a formatting-only capability.
- **Plus zero-cost**: `docker compose config -q` per compose file in
  pre-commit/CI -- schema validation yamllint cannot do (verified
  working on this host; catches typo'd compose keys that today fail
  only at deploy).

## Dockerfile -- hadolint (docker build --check kept as free extra)

Head-to-head on the real Dockerfile: **BuildKit's `--check` (21
structural rules) reported zero warnings; hadolint found 5 issues
including both audit-class findings** (DL3008 unpinned apt x2, DL3013
unpinned pip gunicorn) in 0.03 s. hadolint is the only tool on the
small surfaces that demonstrably catches an audited class. AUR
hadolint-bin on host; hadolint-action or static binary in CI.
Adoption: DL3013 on gunicorn is a real fix (root cause: move gunicorn
into requirements.txt pinning); DL3008 conflicts with the deliberate
apt-upgrade currency pattern -- ignore with rationale in
.hadolint.yaml.

## SQL -- nothing (sqlfluff REJECTED for now, with a revisit trigger)

sqlfluff 4.2.2 is one `pacman -S` away (easy was right) -- but
verified against the actual files it **hard-fails to parse
`init_db_role.sql`** (PRS unparsable: the psql `:'var'` substitution
syntax), there were zero SQL findings in the audit, and the two files
(176 lines) are near-static bootstrap DDL. Adopting a linter that
cannot parse half its surface fails the no-band-aid rule. Revisit
trigger, recorded: "if a third hand-written SQL file lands, or the
psql-var pattern is removed, re-evaluate sqlfluff with templater
config." (squawk: lock-safety niche for live SQL migrations -- absent
here, Alembic migrations are Python. pgFormatter: format-only churn.)

## Markdown -- rumdl (incumbent affirmed)

rumdl 0.2.14 (pacman) with the existing `.rumdl.toml`; tracks the
markdownlint MD0xx rule set; Rust binary fits the hook budget. Nothing
markdownlint-cli2 (Node) or mdformat adds justifies switching away
from the established choice. 211 files / 195k lines -- run the baseline
before locking a floor.

## Critic additions (cross-surface)

- **gitleaks (ADOPT, pacman)**: the one catastrophic class the whole
  stack leaves unwatched -- a committed credential, with no human
  reviewer behind the solo operator. One-time allowlist pass for test
  fixtures (the 2FA/TOTP test corpus will have dummy secrets).
- **typos (ADOPT, pacman extra/typos 1.47.2)**: content-level checking
  for the 195k-line Markdown estate + comments; weakest adopt (zero
  audit findings in class); PIN the version in CI -- dictionary updates
  can newly flag unchanged files.
- **ast-grep (CONSIDER, later)**: the credible house-rules engine for
  JS/CSS/HTML if needs outgrow Biome's GritQL plugins. Zero rules exist
  today -- adopting now violates no-gold-plating.
- **semgrep (SKIP)**: seconds-class cold start vs tens-of-ms hook
  budget, not in pacman. **editorconfig-checker (SKIP)**: no
  .editorconfig exists; pure overlap. **lychee (SKIP)**: network-flaky
  for a deterministic-floor stack; --offline mode marginal here.

## The adopted stack (pending operator sign-off)

| Surface | Tool | Install | Hook cost |
|---|---|---|---|
| JS + CSS (+JSON) | biome 2.3.x | pacman / pinned binary in CI | ~36-44 ms (measured) |
| Shell | shellcheck + 2 optional groups; shfmt -i 4 -ci -bn | pacman | ~40-90 ms + 2 ms (measured) |
| Jinja | djlint 1.39.2 lint-only + jinja2 parse script | pip (requirements-dev.txt) | sub-second (Python) |
| Workflows | actionlint + zizmor | pacman | fast (Go/Rust) |
| YAML | yamllint + docker compose config -q | pacman / built-in | fast |
| Dockerfile | hadolint (+ build --check in CI) | AUR hadolint-bin / action | 30 ms (measured) |
| Markdown | rumdl (incumbent) + typos | pacman (installed) | fast (Rust) |
| Secrets | gitleaks | pacman | pre-commit/CI |
| SQL | none (revisit trigger recorded) | -- | -- |

## Decision log

| Date | Decision |
|---|---|
| 2026-06-12 | Comparison complete (web-verified + locally benchmarked). Stack above proposed; sqlfluff rejected with revisit trigger despite pacman availability (parse failure on psql-var syntax); hadolint adopted over docker build --check on head-to-head evidence; zizmor added (catches both workflow audit findings; actionlint catches neither). Operator sign-off pending. |
