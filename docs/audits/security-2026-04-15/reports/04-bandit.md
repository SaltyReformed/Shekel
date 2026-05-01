# 04 -- Bandit SAST Analysis

Scanner: bandit 1.9.4 (running on Python 3.14.4 in the scratch venv).

Commands run:

```
.audit-venv/bin/bandit -r app/ -f json -o docs/audits/security-2026-04-15/scans/bandit.json
.audit-venv/bin/bandit -r app/ -f txt  -o docs/audits/security-2026-04-15/scans/bandit.txt
```

Scope: recursive scan of every `.py` file under `app/`. Tests were not in
scope (bandit's `-r app/` does not reach `tests/`, which is the correct
split for this audit -- test code that uses `assert` or `random` is
expected).

## Summary

- **Total issues found:** 5
- **By severity:** 0 High, 3 Medium, 2 Low, 0 Undefined
- **By confidence:** 5 High, 0 Medium, 0 Low
- **Lines of code scanned:** 24,843
- **Classification breakdown:** 2 Real (but already known), 3 Noise
  (false positives), 0 Needs-human-review
- **New findings not surfaced in Section 1A:** 0
- **Top concern:** Bandit found nothing the Section 1A subagents did not
  already catch. Zero High findings over ~25k lines of Python is a
  genuinely clean result for the canonical "common Python security
  anti-pattern" rule set.

## Finding-by-finding analysis

### B110-1: try/except pass in `_convert_percentage_inputs`

- **Bandit:** B110 `try_except_pass`, Low severity, High confidence
- **CWE:** CWE-703 (Improper Check or Handling of Exceptional Conditions)
- **Location:** `app/routes/investment.py:813:12`
- **Evidence:**
  ```python
  try:
      data[field] = str(Decimal(data[field]) / Decimal("100"))
  except Exception:
      pass
  return data
  ```
- **Classification:** **Real (already known).**
- **Analysis:** Broad `except Exception: pass` over a Decimal percentage
  parse. On a bad input the unconverted raw string flows to Marshmallow,
  which then surfaces a confusing validation error downstream. This is
  not a security bug; it is a CLAUDE.md rule 1 violation ("Do not use
  broad `except Exception`") and a UX bug.
- **Cross-reference:** Subagent B1 flagged the sister pattern in
  `app/routes/retirement.py:292-297` as finding **F-B1-12** (Info) and
  explicitly named the `investment.py:813` twin in its recommendation:
  > "A similar `except Exception: pass` sits at
  > `app/routes/investment.py:813`, `_convert_percentage_inputs`, for
  > the same reason and should be narrowed in the same fix."
  Bandit (Low) and B1 (Info) agree the issue is real and low-impact.
- **Recommendation:** Narrow to
  `except (InvalidOperation, ValueError, ArithmeticError):` and leave
  the field untouched on failure so Marshmallow surfaces a specific
  validation error. Fold into the same commit that fixes F-B1-12.

### B110-2: try/except pass in `retirement.update_settings`

- **Bandit:** B110 `try_except_pass`, Low severity, High confidence
- **CWE:** CWE-703
- **Location:** `app/routes/retirement.py:296:12`
- **Evidence:**
  ```python
  try:
      form_data[field] = str(Decimal(form_data[field]) / Decimal("100"))
  except Exception:
      pass
  ```
- **Classification:** **Real (already known).**
- **Analysis:** Same pattern as B110-1. Same impact. Same fix.
- **Cross-reference:** Subagent B1 finding **F-B1-12**. This is the
  primary location named in B1's title.
- **Recommendation:** Narrow the except clause. Fold into the F-B1-12
  fix commit.

### B704-1: Markup in `salary.create_profile` error branch

- **Bandit:** B704 `markupsafe_markup_xss`, Medium severity, High confidence
- **CWE:** CWE-79 (Cross-Site Scripting)
- **Location:** `app/routes/salary.py:179:14`
- **Evidence:**
  ```python
  flash(Markup(
      'You need an active account before creating a salary profile. '
      '<a href="' + url_for("accounts.new_account") + '" class="alert-link">'
      'Create an account</a>.'
  ), "danger")
  ```
- **Classification:** **Noise (false positive).**
- **Analysis:** Bandit pattern-matches any string concatenation inside
  `Markup(...)` and flags it as potential XSS. Here the concatenated
  value is `url_for("accounts.new_account")` sandwiched between two
  fixed string literals. `url_for` takes a blueprint-and-endpoint name
  (compile-time constants) and returns a server-generated URL path.
  There is no HTTP request parameter, no form input, no template
  variable, no user-controlled state anywhere in the construction. An
  attacker cannot influence `url_for`'s output through any route into
  this handler. The `Markup` wrapper exists only to prevent Jinja from
  escaping the `<a>` tag into visible HTML characters.
- **Cross-reference:** Subagent B1 cleared this handler explicitly in
  its "|safe / Markup usage" check section:
  > "three `flash(Markup(...))` calls that embed fixed HTML
  > `<a href="...">` links built from `url_for(...)`. No user input
  > interpolated. **Safe.**"
- **Recommendation:** No code change required. If you ever wire bandit
  into CI and want the line to stop showing up, either (a) refactor
  to `flash_html_link("message", url_for("..."))` helper that builds
  the Markup internally via a template rendering step, or (b) add
  `# nosec B704 -- url_for output is server-controlled` to the line.
  Option (a) is cleaner; option (b) is the lower-diff escape hatch.
  Purely cosmetic.

### B704-2: Markup in `salary.list_profiles` no-period branch

- **Bandit:** B704 `markupsafe_markup_xss`, Medium severity, High confidence
- **CWE:** CWE-79
- **Location:** `app/routes/salary.py:662:14`
- **Evidence:**
  ```python
  flash(Markup(
      'No pay periods found. '
      '<a href="' + url_for("pay_periods.generate_form") + '" class="alert-link">'
      'Generate pay periods</a> first.'
  ), "warning")
  ```
- **Classification:** **Noise (false positive).**
- **Analysis:** Identical structural pattern to B704-1. `url_for(
  "pay_periods.generate_form")` is server-generated. No user input in
  the concat.
- **Cross-reference:** Same B1 clean section as B704-1.
- **Recommendation:** Same as B704-1.

### B704-3: Markup in `templates.preview_recurrence`

- **Bandit:** B704 `markupsafe_markup_xss`, Medium severity, High confidence
- **CWE:** CWE-79
- **Location:** `app/routes/templates.py:584:11`
- **Evidence:**
  ```python
  return Markup(html)
  ```
  `html` is built upstream in the same function from values of the
  form `p.start_date.strftime(...)` and `p.end_date.strftime(...)`,
  where `p` is a `PayPeriod` row loaded via a user-scoped query.
- **Classification:** **Noise (false positive).**
- **Analysis:** `html` is a concatenation of date strings produced by
  `datetime.strftime(...)`. `strftime` returns a format-string expansion
  of date field values -- year, month, day integers. It cannot produce
  `<` or `>` characters regardless of the underlying `date`. Therefore
  the `Markup(html)` call cannot contain HTML from a user-controlled
  source.
- **Cross-reference:** Subagent B1 cleared this specific call
  explicitly:
  > "`preview_recurrence` returns a Markup string built from
  > `p.start_date.strftime(...)` and `p.end_date.strftime(...)`, i.e.
  > formatted dates from DB rows. Dates are not user-controlled
  > strings. **Safe.**"
- **Recommendation:** No code change required. Same `# nosec B704`
  option available if bandit enters CI.

## What bandit did NOT find -- clean negative results

Bandit's rule set covers roughly 60 distinct patterns. Running the full
set against 24,843 lines of code with zero hits in each of the
categories below is a positive signal worth recording for the Section
S8 consolidator:

- **B102 `exec_used` / B307 `eval`:** no dynamic code execution anywhere.
- **B301 `pickle` / B302 `marshal` / B506 `yaml_load` / B614 `pytorch_load_save`:**
  no unsafe deserialization.
- **B303 `md5` / B304 / B305 (weak cipher modes):** no deprecated
  cryptographic primitives. Password hashing uses bcrypt (confirmed by
  Subagent A). Fernet is AES-128-CBC-HMAC, which is current.
- **B324 `hashlib_new_insecure_functions`:** no `hashlib.new("md5")`
  tricks.
- **B501 / B502 / B503 (SSL verification disabled, weak protocols in
  `requests`, `urllib3`, `http.client`):** no hits. No HTTP client in
  the application code makes outbound requests with verification
  turned off.
- **B602 / B603 / B604 / B605 / B607 (subprocess with `shell=True` or
  partial paths):** no hits. A Glob of `app/` for `subprocess` returns
  zero matches -- Shekel's application code does not shell out to any
  external process.
- **B608 `hardcoded_sql_expressions`:** no hits. The only `db.text(...)`
  usage in the application is `SELECT 1` in `app/routes/health.py:39`,
  which is a parameter-free connectivity probe and bandit correctly
  accepted.
- **B101 `assert_used`:** no hits in `app/`. Asserts live in `tests/`
  (out of scope for this scan) where they are expected.
- **B105-B107 `hardcoded_password_*`:** no hits. No literal string that
  matches bandit's heuristic for an embedded password or token.
- **B201 `flask_debug_true`:** no hits. `DEBUG=True` is only set in
  `DevConfig`, and bandit correctly scopes around the class gate.
- **B113 `request_without_timeout`:** no hits because no outbound HTTP
  requests exist.

## Bottom line

Bandit's output is entirely duplicative of Subagent B1's findings:

- **2 Real entries** map directly onto B1 finding **F-B1-12** (the
  `retirement.py` and `investment.py` broad-except pair). Fix once, both
  disappear.
- **3 Noise entries** were already explicitly cleared by B1 in its
  "|safe / Markup usage in render context" check section. They are
  false positives -- bandit does not know that `url_for` and `strftime`
  are not user-controlled.
- **Zero new findings** are introduced to the audit backlog from this
  scan.

The bandit JSON and txt outputs are retained under
`docs/audits/security-2026-04-15/scans/` as scan-inventory artifacts for
Session S8's consolidator. If bandit is ever wired into CI, the three
B704 false positives should be silenced with `# nosec B704` comments
(or preferably refactored through a small helper that hides the
`Markup` call from bandit) so the CI run stays clean.

## Open questions for the developer

1. **Does bandit become a CI gate?** Running bandit at CI time over
   `app/` would catch regressions where a developer adds an `eval()`,
   a `subprocess(shell=True)`, or a bare `except Exception: pass`. The
   run is fast (well under a second for Shekel). The only maintenance
   overhead is silencing the three B704 false positives. Not a
   finding; a workflow question.
2. **Is there a reason the two Decimal-percentage broad excepts exist
   instead of letting Marshmallow reject the bad value?** The pattern
   looks like a workaround for Marshmallow's HTML-form handling -- the
   route pre-converts "5" to "0.05" before the schema sees it, and
   swallows the error if the pre-conversion fails. A cleaner approach
   is a Marshmallow `@pre_load` hook on the schema itself. Worth
   revisiting when the F-B1-12 fix is scheduled.
