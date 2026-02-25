● 1️⃣ Executive Summary                                                                        
                                                                                              
  Overall UX Score: 72/100                                                                    
  Overall Accessibility Score: 48/100                                                         
                                                                                              
  The Shekel app demonstrates strong foundational UX with a well-implemented two-tier grid    
  editing system, comprehensive keyboard navigation (arrow keys, Tab, Enter, Escape, F2), and 
  a polished dark theme with light mode toggle. HTMX integration is consistent and the sticky 
  header/footer/column pattern works well for the spreadsheet metaphor.                       
                                                                                              
  However, accessibility is significantly below WCAG 2.1 AA compliance. The app relies heavily
   on color-only status indicators, lacks ARIA semantics on interactive grid elements, has no 
  skip navigation, no aria-live regions for dynamic HTMX updates, and no form validation error
   display strategy. Screen reader users would struggle with the grid entirely.

  Top 5 Critical Issues:
  1. Grid cells with role="button" lack tabindex, aria-label, and keyboard event handlers in
  HTML (JS handles some)
  2. No aria-live regions — HTMX-driven balance updates and cell swaps are invisible to
  assistive technology
  3. No form validation error display — Marshmallow validates server-side but errors never
  render visually
  4. No custom error pages (404/500) — users see raw text responses
  5. Color alone conveys meaning (negative balance red, low balance orange, status badge
  colors) with no text/icon fallback

  Quick Wins (<1 hour):
  - Add lang attribute ✓ (already present), add skip-to-content link
  - Add aria-live="polite" to #grid-summary and #txn-popover
  - Add autocomplete attributes to login form
  - Add autofocus to first field in Add Transaction modal
  - Add aria-label to icon-only navigation buttons (chevron left/right)

  ---
  2️⃣ Heuristic & WCAG Scorecard

  Nielsen's 10 Usability Heuristics

  Category: Usability
  Standard: H1: Visibility of System Status
  Score (0–5): 3
  Issues Found: HTMX updates lack feedback for AT users; no loading indicators; save-flash
    animation is good
  Severity: Medium
  Notes: Balance row refreshes silently; no spinner/skeleton
  ────────────────────────────────────────
  Category: Usability
  Standard: H2: Match Between System and Real World
  Score (0–5): 4
  Issues Found: Period labels (3P, 6P, 13P) are domain-specific but clear; currency formatting

    is excellent
  Severity: Low
  Notes: "True-up" and "Carry Fwd" may confuse new users
  ────────────────────────────────────────
  Category: Usability
  Standard: H3: User Control and Freedom
  Score (0–5): 4
  Issues Found: Escape cancels edits; undo for credit card marking exists; soft-delete
    preserves data
  Severity: Low
  Notes: No undo for transaction deletion or carry-forward
  ────────────────────────────────────────
  Category: Usability
  Standard: H4: Consistency and Standards
  Score (0–5): 4
  Issues Found: Bootstrap patterns used consistently; HTMX conventions uniform across routes
  Severity: Low
  Notes: Some forms POST, others PATCH/DELETE — correct REST but inconsistent user experience
  ────────────────────────────────────────
  Category: Usability
  Standard: H5: Error Prevention
  Score (0–5): 2
  Issues Found: confirm() dialogs on delete; but no input constraints on amounts; no duplicate

    detection on categories
  Severity: High
  Notes: Missing client-side validation; can submit empty/invalid forms
  ────────────────────────────────────────
  Category: Usability
  Standard: H6: Recognition Rather Than Recall
  Score (0–5): 3
  Issues Found: F2 and keyboard shortcuts exist but are completely undiscoverable; no
    help/tooltip system
  Severity: Medium
  Notes: Users must know shortcuts exist
  ────────────────────────────────────────
  Category: Usability
  Standard: H7: Flexibility and Efficiency of Use
  Score (0–5): 5
  Issues Found: Excellent — two-tier editing, keyboard grid nav, period quick-select,
    click-to-create
  Severity: —
  Notes: Best aspect of the app
  ────────────────────────────────────────
  Category: Usability
  Standard: H8: Aesthetic and Minimalist Design
  Score (0–5): 4
  Issues Found: Clean dark theme; good typography (Inter + JetBrains Mono); minimal clutter
  Severity: Low
  Notes: Grid can feel dense at 26+ periods
  ────────────────────────────────────────
  Category: Usability
  Standard: H9: Help Users Recognize/Recover from Errors
  Score (0–5): 1
  Issues Found: No visible error messages in any form; server returns JSON errors or flash
    messages that may not display for HTMX
  Severity: Critical
  Notes: Validation errors are swallowed silently
  ────────────────────────────────────────
  Category: Usability
  Standard: H10: Help and Documentation
  Score (0–5): 1
  Issues Found: No help page, no keyboard shortcut reference, no onboarding, no tooltips
  beyond
    title attrs
  Severity: High
  Notes: New users have no guidance

  WCAG 2.1 AA by POUR Principles

  Category: Perceivable
  Standard: 1.1 Text Alternatives
  Score (0–5): 2
  Issues Found: Icon-only buttons lack text alternatives; status badges use symbols (✓, CC)
    without aria-label in many places
  Severity: High
  Notes: Chevron nav, theme toggle, action buttons
  ────────────────────────────────────────
  Category: Perceivable
  Standard: 1.3 Adaptable
  Score (0–5): 2
  Issues Found: Grid table uses scope="col" but complex layout with sticky cols/merged cells
    lacks headers/id associations
  Severity: High
  Notes: Screen readers can't navigate grid meaningfully
  ────────────────────────────────────────
  Category: Perceivable
  Standard: 1.4 Distinguishable
  Score (0–5): 3
  Issues Found: Color contrast likely passes in dark theme (Steel Blue on dark bg); but
    color-only status indicators fail 1.4.1
  Severity: Medium
  Notes: Negative balance, low balance, done/credit badges
  ────────────────────────────────────────
  Category: Operable
  Standard: 2.1 Keyboard Accessible
  Score (0–5): 3
  Issues Found: JS keyboard nav exists but HTML elements lack tabindex; role="button" divs not

    natively focusable
  Severity: High
  Notes: Good JS implementation, poor HTML semantics
  ────────────────────────────────────────
  Category: Operable
  Standard: 2.4 Navigable
  Score (0–5): 1
  Issues Found: No skip link; no landmark roles beyond nav; no page titles differentiated per
    view; no focus order management
  Severity: Critical
  Notes: <title> is same pattern but exists per page
  ────────────────────────────────────────
  Category: Operable
  Standard: 2.5 Input Modalities
  Score (0–5): 4
  Issues Found: Click, keyboard, and touch all supported; no gesture-only interactions
  Severity: Low
  Notes: Good
  ────────────────────────────────────────
  Category: Understandable
  Standard: 3.1 Readable
  Score (0–5): 4
  Issues Found: lang="en" present; clear language; good typography
  Severity: Low
  Notes: Some jargon (true-up, anchor)
  ────────────────────────────────────────
  Category: Understandable
  Standard: 3.2 Predictable
  Score (0–5): 3
  Issues Found: HTMX swaps are consistent; but onchange navigation on salary breakdown is
    unexpected
  Severity: Medium
  Notes: Select changes trigger navigation without warning
  ────────────────────────────────────────
  Category: Understandable
  Standard: 3.3 Input Assistance
  Score (0–5): 1
  Issues Found: No visible error messages; no aria-invalid; no aria-describedby on form hints;

    no required field indicators
  Severity: Critical
  Notes: Worst accessibility gap
  ────────────────────────────────────────
  Category: Robust
  Standard: 4.1 Compatible
  Score (0–5): 2
  Issues Found: No ARIA landmarks; role="button" without full ARIA contract; HTMX fragments
  may
    break AT parsing
  Severity: High
  Notes: Dynamic content insertion without AT notification

  ---
  3️⃣ Detailed Findings Table

  ID: A1
  File: base.html
  Issue: No skip-to-content link
  Standard Violated: WCAG 2.4.1
  Why It Matters: Keyboard users must tab through entire nav on every page
  Fix: Add <a href="#main-content" class="visually-hidden-focusable">Skip to content</a>
  before
    nav; add id="main-content" to <main>
  Priority: High
  Effort: XS
  ────────────────────────────────────────
  ID: A2
  File: base.html
  Issue: No ARIA landmark roles
  Standard Violated: WCAG 1.3.1
  Why It Matters: Screen readers can't navigate by landmarks
  Fix: Add role="banner" to nav, role="main" to main container, role="contentinfo" to footer
    (if any)
  Priority: High
  Effort: XS
  ────────────────────────────────────────
  ID: A3
  File: grid/grid.html
  Issue: Icon-only nav buttons (chevron left/right) lack accessible names
  Standard Violated: WCAG 1.1.1
  Why It Matters: Screen readers announce empty button
  Fix: Add aria-label="Show earlier periods" and aria-label="Show later periods"
  Priority: High
  Effort: XS
  ────────────────────────────────────────
  ID: A4
  File: grid/grid.html
  Issue: Add Transaction modal no autofocus
  Standard Violated: WCAG 2.4.3, H5
  Why It Matters: Focus lands on close button, not first field
  Fix: Add autofocus to name input in modal
  Priority: Medium
  Effort: XS
  ────────────────────────────────────────
  ID: A5
  File: grid/grid.html
  Issue: Period quick-select buttons have no aria-current
  Standard Violated: WCAG 4.1.2
  Why It Matters: Active period count not announced
  Fix: Add aria-current="true" to the active btn-primary button
  Priority: Low
  Effort: XS
  ────────────────────────────────────────
  ID: A6
  File: grid/_transaction_cell.html
  Issue: Clickable div with no tabindex or aria-label
  Standard Violated: WCAG 2.1.1, 4.1.2
  Why It Matters: Not keyboard accessible via HTML alone; screen readers can't describe cell
  Fix: Add tabindex="0" and aria-label="{{ txn.name }}: ${{ txn.display_amount }}"
  Priority: High
  Effort: S
  ────────────────────────────────────────
  ID: A7
  File: grid/_transaction_empty_cell.html
  Issue: role="button" div lacks tabindex="0"
  Standard Violated: WCAG 2.1.1
  Why It Matters: Not reachable via Tab
  Fix: Add tabindex="0" and onkeydown for Enter/Space
  Priority: High
  Effort: XS
  ────────────────────────────────────────
  ID: A8
  File: grid/_balance_row.html
  Issue: Color-only balance warnings (red/orange)
  Standard Violated: WCAG 1.4.1
  Why It Matters: Color-blind users can't distinguish
  Fix: Add icon prefix: <i class="bi bi-exclamation-triangle-fill"></i> for negative, <i
    class="bi bi-exclamation-circle"></i> for low
  Priority: High
  Effort: S
  ────────────────────────────────────────
  ID: A9
  File: grid/_balance_row.html
  Issue: No aria-live on dynamic tfoot
  Standard Violated: WCAG 4.1.3
  Why It Matters: Balance updates invisible to AT
  Fix: Add aria-live="polite" aria-atomic="true" to <tfoot id="grid-summary">
  Priority: High
  Effort: XS
  ────────────────────────────────────────
  ID: A10
  File: grid/grid.html
  Issue: #txn-popover dynamic content not announced
  Standard Violated: WCAG 4.1.3
  Why It Matters: Popover appears without AT notification
  Fix: Add aria-live="assertive" to #txn-popover, role="dialog" with aria-label
  Priority: High
  Effort: S
  ────────────────────────────────────────
  ID: A11
  File: All forms
  Issue: No validation error display
  Standard Violated: WCAG 3.3.1, H9
  Why It Matters: Users submit invalid data with no feedback; errors returned as JSON or flash

    but never rendered inline
  Fix: Create _form_errors.html macro; add aria-invalid="true" and error <div> linked via
    aria-describedby
  Priority: Critical
  Effort: M
  ────────────────────────────────────────
  ID: A12
  File: All forms
  Issue: form-text hints not linked to inputs
  Standard Violated: WCAG 1.3.1, 3.3.2
  Why It Matters: AT users don't hear help text
  Fix: Add id to each .form-text and aria-describedby to corresponding input
  Priority: Medium
  Effort: S
  ────────────────────────────────────────
  ID: A13
  File: auth/login.html
  Issue: Missing autocomplete attributes
  Standard Violated: WCAG 1.3.5
  Why It Matters: Password managers and AT can't identify fields
  Fix: Add autocomplete="email" and autocomplete="current-password"
  Priority: Medium
  Effort: XS
  ────────────────────────────────────────
  ID: A14
  File: Multiple
  Issue: confirm() dialogs not accessible
  Standard Violated: WCAG 4.1.2, H5
  Why It Matters: Browser confirm() may not be fully accessible; no custom styling
  Fix: Replace with Bootstrap modal confirmation pattern
  Priority: Low
  Effort: M
  ────────────────────────────────────────
  ID: A15
  File: app/__init__.py
  Issue: No custom 404/500 error pages
  Standard Violated: H9
  Why It Matters: Users see raw "Not found" text or stack traces
  Fix: Register @app.errorhandler(404) and 500 with proper templates
  Priority: High
  Effort: S
  ────────────────────────────────────────
  ID: A16
  File: salary/breakdown.html
  Issue: onchange select triggers page navigation
  Standard Violated: WCAG 3.2.2
  Why It Matters: Unexpected context change on input
  Fix: Add explicit "Go" button next to period select, or at minimum add aria-label describing

    the behavior
  Priority: Medium
  Effort: XS
  ────────────────────────────────────────
  ID: A17
  File: app.css
  Issue: Focus ring may not meet 3:1 contrast
  Standard Violated: WCAG 2.4.7
  Why It Matters: .cell-focus outline in accent color on dark bg may be insufficient
  Fix: Verify contrast; consider outline: 2px solid #fff with offset
  Priority: Medium
  Effort: XS
  ────────────────────────────────────────
  ID: A18
  File: Multiple
  Issue: No <caption> on data tables
  Standard Violated: WCAG 1.3.1
  Why It Matters: Tables lack programmatic description
  Fix: Add <caption class="visually-hidden">Budget grid...</caption> to main grid table
  Priority: Medium
  Effort: XS
  ────────────────────────────────────────
  ID: A19
  File: grid/grid.html
  Issue: Carry Forward button has no confirmation
  Standard Violated: H5
  Why It Matters: Destructive action (moves transactions) with no undo
  Fix: Add confirmation modal or confirm() at minimum
  Priority: Medium
  Effort: S
  ────────────────────────────────────────
  ID: A20
  File: base.html
  Issue: No visible keyboard shortcut reference
  Standard Violated: H10, H6
  Why It Matters: F2, Escape, arrow keys, Ctrl+arrows all undiscoverable
  Fix: Add ? keyboard shortcut to show help modal listing all shortcuts
  Priority: Medium
  Effort: M
  ────────────────────────────────────────
  ID: A21
  File: config.py
  Issue: Hardcoded SECRET_KEY and DB credentials in dev config
  Standard Violated: Security best practice
  Why It Matters: Risk of credential leak if committed to public repo
  Fix: Move all secrets to .env file, use python-dotenv
  Priority: High
  Effort: S
  ────────────────────────────────────────
  ID: A22
  File: app/__init__.py
  Issue: No CSRF protection
  Standard Violated: OWASP A5
  Why It Matters: Forms vulnerable to CSRF attacks
  Fix: Add Flask-WTF CSRFProtect or manual CSRF tokens
  Priority: High
  Effort: M
  ────────────────────────────────────────
  ID: A23
  File: grid/_transaction_quick_create.html
  Issue: Input has no visible label
  Standard Violated: WCAG 1.3.1, 3.3.2
  Why It Matters: Placeholder "0.00" disappears on focus; AT may not announce purpose
  Fix: Add aria-label="Transaction amount"
  Priority: High
  Effort: XS
  ────────────────────────────────────────
  ID: A24
  File: grid/_transaction_quick_edit.html
  Issue: Edit input has no visible label
  Standard Violated: WCAG 1.3.1, 3.3.2
  Why It Matters: Same as A23
  Fix: Add aria-label="Edit amount"
  Priority: High
  Effort: XS
  ────────────────────────────────────────
  ID: A25
  File: Multiple tables
  Issue: No scope="row" on row headers
  Standard Violated: WCAG 1.3.1
  Why It Matters: Category name cells act as row headers but aren't marked as such
  Fix: Change category <td> to <th scope="row"> in grid rows
  Priority: Medium
  Effort: S

  ---
  4️⃣ Pattern-Level Architecture Review

  Template Structure

  Strengths: Single base.html with consistent {% block content %} / {% block scripts %}
  pattern. Partials prefixed with _ (e.g., _balance_row.html, _transaction_cell.html). Clean
  separation of grid fragments for HTMX swaps.

  Improvements needed:
  - No Jinja2 macros exist — form field rendering is duplicated across templates (labels,
  input groups, error display all hand-coded each time). A _form_macros.html with
  render_field(), render_select(), render_input_group() would eliminate ~200 lines of
  duplication.
  - No shared error display partial — each form would need individual error handling without a
   macro.

  Form Handling & Validation UX

  Current: Marshmallow schemas validate on the server. Routes check errors =
  schema.validate(request.form) and return JSON errors or redirect with flash. No template
  ever renders field-level errors. HTMX responses return 400 status but the error JSON is
  never displayed to the user.

  Gap: This is the single largest UX hole. Users can submit invalid data and see nothing
  happen (HTMX swallows the error response).

  Flash Messaging

  Current: base.html iterates get_flashed_messages(with_categories=true) and renders Bootstrap
   alerts. This works for full-page form submissions (templates, salary, settings) but not for
   HTMX interactions where only fragments are returned.

  Gap: HTMX-driven operations (grid edits, inline creates, balance true-up) bypass flash
  messages entirely.

  Navigation Consistency

  Good: All 6 nav items present on every page. Active state uses request.endpoint.startswith()
   matching. User context displayed. Logout always available.

  Gap: No breadcrumbs on deep pages (salary breakdown, template edit). No "back" affordance
  beyond browser back button.

  Blueprint Organization

  Excellent: Clean separation — auth, grid, transactions, templates, pay_periods, accounts,
  categories, settings, salary. Each blueprint handles its own domain. Route files are 100-300
   lines, manageable. Services layer cleanly separated from HTTP concerns.

  Config & Security

  Concerns:
  - No CSRF protection (Flask-WTF not installed, htmx:configRequest has placeholder but no
  token)
  - Hardcoded SECRET_KEY in dev config
  - No rate limiting on login
  - No Content-Security-Policy headers
  - No X-Frame-Options or X-Content-Type-Options headers

  ---
  5️⃣ Prioritized Implementation Roadmap

  Phase 1 — Critical Accessibility & UX (High Impact, Low Effort)

  1. Add skip-to-content link (A1) — base.html — XS
  2. Add aria-label to icon-only buttons (A3, A6, A7, A23, A24) — grid templates — S
  3. Add aria-live to dynamic regions (A9, A10) — grid.html, _balance_row.html — XS
  4. Add autocomplete to login form (A13) — auth/login.html — XS
  5. Add autofocus to modal first input (A4) — grid.html — XS
  6. Add tabindex="0" to button-role divs (A6, A7) — grid cell templates — XS
  7. Add color-independent status indicators (A8) — _balance_row.html — S
  8. Add aria-label to quick edit/create inputs (A23, A24) — grid partials — XS
  9. Add <caption> to data tables (A18) — grid, salary, templates tables — XS
  10. Change category cells to <th scope="row"> (A25) — grid.html — S
  11. Register custom 404/500 error handlers (A15) — app/__init__.py + 2 new templates — S
  12. Add aria-describedby to form hints (A12) — all forms with .form-text — S

  Files to modify: base.html, grid/grid.html, grid/_balance_row.html,
  grid/_transaction_cell.html, grid/_transaction_empty_cell.html,
  grid/_transaction_quick_create.html, grid/_transaction_quick_edit.html, auth/login.html,
  app/__init__.py
  Expected outcome: WCAG 2.1 AA score improves from ~48 to ~68; screen reader users can
  navigate the grid; keyboard-only use fully functional.

  Phase 2 — Structural Improvements

  1. Create form validation error display system (A11) — Create _form_macros.html with
  render_field() macro; update all forms to use it; add aria-invalid and inline error
  rendering — M
  2. Add CSRF protection (A22) — Install Flask-WTF; add CSRFProtect(app); inject token via
  HTMX configRequest — M
  3. Create Jinja2 form macros — Extract repeated form patterns (label + input + help text +
  error) into reusable macros — M
  4. Add keyboard shortcut help modal (A20) — base.html + new _keyboard_help.html partial;
  trigger on ? key — S
  5. Replace confirm() with Bootstrap modals (A14) — Create reusable confirmation modal
  component — M
  6. Add explicit navigation button to salary breakdown select (A16) — S
  7. Move secrets to .env (A21) — Add python-dotenv, update config.py — S

  Files to modify: All form templates, app/__init__.py, app/config.py, base.html,
  app/static/js/app.js
  Expected outcome: Proper form validation UX; CSRF protection; accessibility score ~80;
  reduced template duplication.

  Phase 3 — Polish & Optimization

  1. Add security headers middleware — CSP, X-Frame-Options, X-Content-Type-Options via
  @app.after_request
  2. Add breadcrumb navigation for deep pages (salary breakdown, template edit, tax config)
  3. Improve mobile grid experience — Consider card-based layout below 768px instead of
  cramped table
  4. Add loading states — Skeleton screens or spinners during HTMX requests
  5. Add onboarding flow — First-login wizard (create account → set pay periods → add salary →
   add templates)
  6. Verify focus ring contrast (A17) — Audit all custom focus styles against WCAG 2.4.7
  7. Add toast notifications for HTMX operations (save confirmation, carry-forward success)
  8. Rate limit login route — Flask-Limiter or manual rate limiting

  Expected outcome: Production-ready security posture; polished user experience; WCAG AA
  compliance ~90+.