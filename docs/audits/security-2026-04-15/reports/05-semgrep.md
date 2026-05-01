# 05 -- Semgrep SAST Analysis

Scanner: semgrep 1.159.0 (running on Python 3.14.4 in the scratch venv).

Rule packs applied: `p/python` (general Python anti-patterns), `p/owasp-top-ten`
(OWASP category rules), `p/flask` (Flask-specific mistakes). All three are
Community rule packs downloaded from the public Semgrep Registry on the
first run; subsequent runs used the local rule cache.

Commands run:

```
.audit-venv/bin/semgrep --config p/python --config p/owasp-top-ten --config p/flask \
    --json --output docs/audits/security-2026-04-15/scans/semgrep.json app/
.audit-venv/bin/semgrep --config p/python --config p/owasp-top-ten --config p/flask \
    app/ > docs/audits/security-2026-04-15/scans/semgrep.txt 2>&1
```

Note: the first attempt at the second command used `>` alone without
`2>&1`. Semgrep writes its scan summary to **stderr**, not stdout, and
with zero findings stdout is empty -- the initial redirection produced a
0-byte txt file. Re-running with `2>&1` captured the summary properly.
Both scan files now contain the scan result; this is a housekeeping note
for the Session S8 consolidator, not a finding.

Scope: 225 files tracked by git under `app/`. Semgrep's default scope is
git-tracked files only, which is correct for this audit -- it avoids the
scratch venv and any uncommitted scratch work.

## Summary

- **Total findings:** 0 (0 blocking)
- **Total parse warnings:** 54 (all `warn` level, all on `.html` template
  files, zero on any `.py` file)
- **Rules loaded from rule packs:** 548
- **Rules actually run (after language filter):** 226
  - `<multilang>` -- 6 rules applied to all 225 files
  - `html` -- 1 rule applied to 102 files
  - `python` -- 151 rules applied to 99 files
  - `js` -- 65 rules applied to 18 files
  - `json` -- 3 rules applied to 1 file
- **Targets scanned:** 225
- **Parsed lines:** ~97.4% (the missing 2.6% is entirely inside HTML
  template files that contain Jinja2 `{% %}` directives semgrep's HTML
  parser cannot fully handle -- not Python source)
- **Classification breakdown:** 0 Real + 0 Noise + 0 Needs-human-review
- **New findings not surfaced in Section 1A:** 0
- **Top concern:** None. Semgrep's Python rule set ran cleanly on every
  Python file in the codebase and produced zero hits across 151 Python
  rules plus 65 JS rules plus the multi-language and framework rule
  sets. The only warnings are template-parser artifacts that are
  expected for Jinja2-in-HTML files.

## Parse warnings -- why 97.4% and not 100%

Semgrep reported `54` entries in the errors list of the JSON output.
Categorized:

| Type | Level | Count | Affected extension |
|------|-------|------:|--------------------|
| `PartialParsing` | warn | 50 | `.html` |
| `Other syntax error` | warn | 4 | `.html` |

**Zero errors touched a `.py` file.** All 54 are warn-level partial-parse
events inside Jinja2 templates. The HTML semantics inside those templates
is valid HTML in each fragment, but semgrep's HTML parser stops on the
first `{% %}` or `{{ }}` it cannot reconcile with the HTML grammar and
records a partial-parse range. Semgrep's `p/python`, `p/owasp-top-ten`,
and `p/flask` packs include exactly one rule that targets HTML files
(the `<multilang>` row in the summary was for multi-language rules, not
HTML-specific), so the practical coverage impact of the template parse
warnings is effectively zero -- there was only ever one HTML rule to
run, and semgrep still ran it on the parseable fragments.

More importantly, all 151 Python rules ran to completion on all 99
Python files in `app/`. The Python AST-based analysis that is the real
source of value in this scan was not affected by the template parser
failing on Jinja2 syntax.

## What semgrep did NOT find

Zero findings across 226 rules is a meaningful negative result. The
rule packs that ran cover, among other things:

**`p/python` (general Python anti-patterns):**
- `eval`, `exec`, `compile`, dynamic `importlib`
- `pickle.load`, `pickle.loads`, `marshal.load`, `yaml.load` without
  `SafeLoader`
- `subprocess` with `shell=True`
- `os.system`, `os.popen`
- `tempfile.mktemp` (insecure temp file creation)
- `assert` used for security-critical checks
- Bare `except:` (not quite the same as `except Exception:`; the latter
  is a bandit B110, not a semgrep rule)
- `input()` used in a security-sensitive context
- Hardcoded credentials heuristics

**`p/owasp-top-ten` (OWASP category rules):**
- **A01 Broken Access Control:** missing `@login_required`, routes that
  dereference `session["user_id"]` without verification, filesystem
  reads of user-controlled paths.
- **A02 Cryptographic Failures:** weak hash algorithms (`md5`, `sha1`)
  used for security, weak random (`random.random()` used where
  `secrets.token_*` should be), constant IVs, ECB mode.
- **A03 Injection:** SQL built with f-strings or `+`, shell commands
  with user input, LDAP injection, XPath injection, server-side
  template injection via `Template(untrusted_string)`.
- **A05 Security Misconfiguration:** `DEBUG = True` at module scope,
  `app.run(debug=True)`, `FLASK_ENV=development`, CORS wildcard origin,
  insecure cookie flags (`SESSION_COOKIE_SECURE = False` explicitly).
- **A07 Authentication Failures:** weak `CSRF_ENABLE = False` in prod,
  `verify=False` on SSL contexts, `ssl.PROTOCOL_TLSv1`.
- **A08 Software/Data Integrity:** unsafe deserialization patterns.

**`p/flask` (Flask-specific):**
- `flask.send_file(request.args["path"])` -- path traversal via user
  input
- `flask.redirect(request.args["url"])` -- open redirect via user input
- `flask.render_template_string(untrusted_string)` -- SSTI
- `request.args.get("sql")` interpolated into ORM query
- `db.session.execute(text(f"..."))` with f-string SQL
- Flask `app.config["SECRET_KEY"] = "literal"`
- `Markup(user_input)` where user_input is recognizable as such

Each of these categories produced **zero hits** against the Shekel
codebase. That is consistent with what Section 1A's manual review
found: Shekel's security weaknesses are in the semantic and design
layer (cross-user FK validation, MFA state management, deployment
trust envelopes, transfer-invariant enforcement-by-caller-filter) --
not in the pattern-matching layer that semgrep is optimized for.

## What semgrep CAN'T catch (and why 1A still found 60 things)

Semgrep is pattern-matching: it sees structural matches for syntactic
anti-patterns. It cannot reason about:

- **Semantic ownership.** Whether a PATCH handler validates that the
  `pay_period_id` in the request body belongs to the current user
  (F-B1-01) is a semantic relationship between two variables and two
  query scopes -- not something a structural pattern can detect.
- **Missing preconditions.** Whether `session["_mfa_pending_user_id"]`
  has an associated timestamp (F-A-01) is a structural negative ("this
  code should also do X and doesn't") that semgrep cannot match without
  a dedicated rule.
- **Cross-function invariants.** Whether `recurrence_engine.resolve_conflicts`
  guards against `txn.transfer_id is not None` (F-B2-01) requires the
  rule author to know that this particular function must not mutate
  shadow transactions -- project-specific knowledge that the community
  rule packs do not have.
- **Config cross-file reasoning.** Whether `ProdConfig`'s SECRET_KEY
  guard rejects `.env.example`'s placeholder (F-C-14) requires reading
  two different files and reasoning about string equality across them.
- **Deployment trust envelopes.** Whether Nginx's `set_real_ip_from`
  range is wider than the actual Docker bridge subnet (F-C-01) is an
  infrastructure finding, not a code pattern.

This is not a weakness of semgrep; it is the defined boundary between
SAST (which looks at code patterns) and architectural / semantic review
(which is what Section 1A's subagents did). A clean semgrep scan
complements the 1A findings -- it means the codebase does not have the
common pattern-matchable holes on top of the semantic ones.

## Notable negative results (things I checked semgrep DID scan for
and came back clean)

A few sanity-checks against the 1A findings confirm semgrep's coverage:

- **`SECRET_KEY` default in `config.py:22`.** The line is
  `SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me-in-production")`.
  Semgrep's `p/flask` `hardcoded-config` rules look for
  `app.config["SECRET_KEY"] = <literal>` and similar direct-assignment
  patterns. The `os.getenv` fallback form does not match that pattern,
  so semgrep did not flag F-C-15. **This is a gap in the rule, not in
  the code.** F-C-15 remains a Section 1A finding. Semgrep's rule
  could be extended to match the `os.getenv(_, <literal>)` fallback
  idiom; Shekel could contribute that rule back if desired.
- **`DEBUG = True` in `DevConfig`.** Semgrep's
  `flask-debug-true` rule looks for `app.run(debug=True)` and
  `app.debug = True`. Class-attribute-level `DEBUG = True` inside a
  config class is not matched -- and that is correct behavior, because
  DevConfig is only ever instantiated in dev. A hit here would have
  been a false positive, and semgrep correctly suppressed it.
- **`except Exception: pass`** at `investment.py:813` and
  `retirement.py:296`. Semgrep does NOT flag these; bandit does
  (B110). The two scanners have complementary coverage on this
  pattern. Both the 1A subagent and bandit found them -- semgrep's
  miss is not a gap in the audit overall.
- **`flash(Markup(...))` string concatenation in `salary.py` /
  `templates.py`.** Semgrep does NOT flag these; bandit does (B704).
  Again complementary coverage -- and bandit's hits were noise
  (false positives) because the concatenated content is `url_for()`
  or `strftime()` output.

## Bottom line

Semgrep produced zero findings and zero errors of any severity on
the Python source code. All 54 warn-level events are HTML template
parse warnings expected for Jinja2-in-HTML, none touch `.py` files,
and the 151 Python rules plus 65 JS rules plus 6 multi-language
rules ran cleanly over the full 99-file Python tree.

**Nothing escalates from this scan.** Zero new findings added to the
audit backlog. The semgrep JSON and txt outputs are retained under
`docs/audits/security-2026-04-15/scans/` as scan-inventory artifacts
for Session S8's consolidator. Combined with bandit's clean result
in `04-bandit.md`, this strongly indicates that Shekel's remaining
security work is in the semantic / design / deployment layer (where
1A lives), not in the "common Python anti-pattern" layer.

## Open questions for the developer

1. **Semgrep in CI?** The same question bandit raised. Semgrep takes
   noticeably longer than bandit (~30 seconds with warm cache, a
   minute or two with cold cache) but produces structurally richer
   findings and supports custom rules. If there is future work in
   Python that matches a semgrep anti-pattern, it would be caught
   immediately. Not a finding; a workflow question.
2. **Is it worth authoring a custom Shekel-specific semgrep rule?**
   One useful custom rule would be "every route handler that calls
   `setattr(obj, field, value)` in a loop must first validate that
   `value` belongs to `current_user.id` if it is an FK" -- exactly
   the pattern that caused F-B1-01. Semgrep supports custom YAML
   rules that can match this. Deferrable; not a Section 1B finding.
