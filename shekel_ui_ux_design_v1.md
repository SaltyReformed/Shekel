# Shekel — UI/UX Design Document

**Version:** 1.0
**Date:** February 22, 2026
**Stack:** Flask · Jinja2 · HTMX · Bootstrap 5 · PostgreSQL

---

## 1. Design Philosophy

### 1.1 Core Principle

Every design decision is measured against one test: **does this make the payday workflow faster and more reliable than the spreadsheet?** The app replaces a 7-year-old biweekly spreadsheet that the user opens multiple times per week. The UI must match or exceed the spreadsheet's speed of use on day one while eliminating the spreadsheet's pain points: fragile formulas, accidental overwrites, and unwieldy horizontal scrolling.

### 1.2 Design Pillars

- **Tool-first density.** Efficient use of screen space with minimal whitespace. Data-dense but not cluttered. Designed for a 24″ monitor as the primary viewport.
- **Keyboard-native interaction.** Tab, Enter, Escape, and arrow-key navigation through the grid. Every action achievable without a mouse. Power-user efficiency is a first-class concern.
- **Spreadsheet familiarity.** Cell-level inline editing, frozen columns, right-aligned numbers, and a tabular grid layout. Users coming from Excel or Google Sheets should feel immediately at home.
- **Instant feedback, zero save buttons.** Every edit saves automatically via HTMX. Edits produce a brief visual flash confirming the save. Balances recalculate in real time.
- **High contrast readability.** Dark and light themes with strong contrast ratios meeting WCAG 2.1 AA (4.5:1 for normal text). Monospace tabular figures for financial data. Status communicated through badges appended to amounts, not by changing text color.

### 1.3 What the App Feels Like

Shekel should feel like a **purpose-built financial cockpit** — dense and efficient like a Bloomberg terminal, but clean and readable rather than overwhelming. It is a single-user tool designed for weekly use, not a consumer onboarding experience. There is no tutorial, no empty-state illustration, and no gamification. The grid loads, the numbers are readable, and the user gets to work.

### 1.4 Spreadsheet Pain Points the App Must Solve

| Pain Point | Spreadsheet | Shekel |
|---|---|---|
| Fragile formulas | Too easy to overwrite a formula or accidentally type over a cell | No exposed formulas; balances calculated server-side as a pure function |
| Accidental overwrites | No protection on data cells | Controlled inline editing with save-on-Enter; Escape to cancel |
| Horizontal sprawl | Gets very wide tracking 2 years out | Column sizing tiers adapt to visible period count; date range controls |
| Manual status tracking | Text labels ("PAID", "CREDIT") typed by hand | Single-click status actions with visual badges |
| No undo for errors | Manual correction or Ctrl+Z if lucky | Undo for reversible actions; confirmation dialogs for destructive ones |

---

## 2. Visual Identity

### 2.1 App Name & Branding

The application is named **Shekel**. The name references the ancient unit of weight and currency, evoking financial precision and permanence. A logo and full brand identity are planned for Phase 6. For Phase 1, the app header displays the name "Shekel" in the primary accent color using a bold sans-serif typeface, serving as a simple wordmark.

### 2.2 Color Palette

The palette uses a **steel blue accent** with a **cool dark base** (blue-gray undertone, similar to VS Code Dark+). Semantic status colors are **fully saturated** for unmistakable at-a-glance reading. All financial amounts use the **same primary text color** regardless of status — status is communicated via small badges, not by changing the amount's color.

#### Primary Accent

| Swatch | Hex | Role | Usage |
|---|---|---|---|
| ![#4A9ECC] | `#4A9ECC` | Primary | Links, focus rings, branding, active elements |
| ![#2878A8] | `#2878A8` | Hover | Button hover states, interactive press states |
| ![#6BB8E0] | `#6BB8E0` | Light | Highlights, badges, subtle emphasis |

#### Semantic — Status

| Swatch | Hex | Role | Usage |
|---|---|---|---|
| ![#2ECC71] | `#2ECC71` | Done / Received | Green ✓ badge appended to paid amounts |
| ![#E67E22] | `#E67E22` | Credit | Amber CC badge appended to credit card amounts |
| ![#E74C3C] | `#E74C3C` | Danger | Red text and red background tint on negative balances |

Status colors appear only as **small badges** (✓ for done/received, CC for credit) appended after the amount. The amount text itself remains in the primary text color. This matches the spreadsheet convention where all values are the same color and status is indicated by a label in the cell.

#### Section Banners

| Context | Dark Theme | Light Theme |
|---|---|---|
| Income banner background | `#1A3D2E` (deep green) | `#D0EDDF` (light green) |
| Income banner text | `#5ED9A0` (bright green) | `#1A6B40` (dark green) |
| Expense banner background | `#3D1A2E` (deep rose) | `#EDCFDE` (light rose) |
| Expense banner text | `#D97BA0` (bright rose) | `#6B1A40` (dark rose) |

#### Dark Theme — Surfaces

| Element | Hex | Notes |
|---|---|---|
| Page background | `#1E2228` | Cool blue-gray undertone, similar to VS Code Dark+ |
| Grid background | `#262B33` | +8 lightness steps from page bg; clean, single surface for all data cells |
| Grid header | `#343B47` | Clearly distinct from grid body |
| Category header rows | `#2A2F38` | Subtle differentiation from data rows |
| Row hover highlight | `#333B4A` | Visible but not jarring; replaces zebra striping |
| Border / Divider | `#4A5568` | Strong enough to be visible as grid lines |
| Border (light) | `#3A4250` | Subtle row separators within sections |
| Summary background | `#1E2228` | Matches page bg to visually separate from data |

#### Dark Theme — Text

| Element | Hex | Notes |
|---|---|---|
| Primary text | `#E2E6EB` | All financial amounts, item names, and body text |
| Secondary text | `#B0B9C6` | Labels, captions, category header names |
| Muted text | `#8892A0` | Strikethrough estimated amounts when actual differs |

#### Light Theme — Surfaces

| Element | Hex | Notes |
|---|---|---|
| Page background | `#EAEFF4` | Noticeably gray; distinct from white grid cells |
| Grid background | `#FFFFFF` | Clean white for data area |
| Grid header | `#343B47` | Same as dark theme for consistency |
| Category header rows | `#F0F3F7` | Light gray differentiation |
| Row hover highlight | `#E8EDF3` | Subtle highlight |
| Border / Divider | `#B0BCCC` | Strong enough to define grid structure |
| Border (light) | `#D0D8E2` | Row separators |
| Summary background | `#F0F4F8` | Slightly off-white to separate from data |

#### Light Theme — Text

| Element | Hex | Notes |
|---|---|---|
| Primary text | `#1E2228` | All amounts and item names |
| Secondary text | `#3E4A5C` | Darkened for comfortable long reading |
| Muted text | `#5A6577` | Strikethrough amounts |

### 2.3 Theme Toggle

The app supports both **dark** and **light** themes. A toggle in the top navigation bar allows switching between themes using a sun/moon icon pair. The selected theme is stored in the user's settings and persists across sessions. The default theme is dark.

Theme switching is implemented via CSS custom properties (variables) controlled by a `data-bs-theme` attribute on the root element. The switch is instant — no full page reload required. All theme-dependent colors are defined as CSS variables so the toggle is purely a class change.

### 2.4 Typography

| Context | Font Family | Weight | Size |
|---|---|---|---|
| UI elements, nav, labels | Inter (fallback: system sans-serif) | 400 / 600 | 14px base |
| Grid amounts (financial) | JetBrains Mono (fallback: Fira Code, monospace) | 400 / 700 | 14px |
| Grid headers | Inter | 600 | 13px |
| Summary / totals row | JetBrains Mono | 700 | 15px |
| Page headings | Inter | 700 | 20–28px |
| Section banners (Income/Expenses) | Inter | 700 | 13px, uppercase, letter-spacing 1.2px |
| Category sub-headers | Inter | 600 | 11px, uppercase, letter-spacing 0.8px |

JetBrains Mono is loaded via Google Fonts (or self-hosted). It provides **tabular figures by default**, meaning all digits are the same width. This guarantees that amounts in the grid align vertically regardless of digit composition, which is critical for financial readability.

### 2.5 Number Formatting

- All financial amounts use the **primary text color** regardless of status.
- Amounts are **right-aligned** within their grid cells.
- **Comma separators** are always displayed for all values (e.g., `1,234` not `1234`).
- Grid cells display amounts **rounded to the nearest whole dollar** for a clean, scannable look. The full decimal value is visible when editing the cell.
- **Dollar signs** are displayed only on summary/total rows and column headers, not on individual line-item cells.
- **Zero values** are displayed as blank (empty cell), matching the spreadsheet convention.
- **Negative values** are displayed in red with a negative sign: `-1,234`.

---

## 3. Layout & Navigation

### 3.1 Page Shell

The app uses a **compact horizontal navigation bar** pinned to the top of the viewport. This keeps navigation accessible without consuming lateral screen space that the grid needs.

The navbar contains:
- **Left:** Shekel wordmark in accent color
- **Center-left:** Primary navigation links
- **Right:** Theme toggle (sun/moon icon), settings gear, logout

**Navigation items (Phase 1):** Budget Grid (default route: `/`), Templates (`/templates`), Categories (`/categories`), Settings (`/settings`).

**Navigation items (added in later phases):** Salary (`/salary`) — Phase 2, Scenarios (`/scenarios`) — Phase 3, Accounts (`/accounts`) — Phase 4, Charts (`/charts`) — Phase 5.

All management pages (templates, categories, settings, salary) are **full separate pages**, not modals or drawers. This keeps the grid context clear and avoids layering complexity.

### 3.2 Grid Page Layout

The budget grid page is the primary view. The page is structured in three vertical zones:

**Top bar:** Anchor balance display (large, prominent, editable inline), pay period date range label, date range quick-select buttons, and left/right navigation arrows. This bar is always visible at the top of the grid area.

**Grid body:** The scrollable table of income and expense rows organized by pay period columns. Income rows sit above expense rows with a colored section banner and a 16px spacer separating them. Category group names appear as sub-headers within each section. The left column (transaction names and category headers) is frozen and always visible during horizontal scrolling.

**Bottom summary bar:** Pinned/sticky footer rows that remain visible regardless of vertical scrolling. These contain Total Income, Total Expenses, Net, and Projected End Balance per period. The End Balance row is the most visually prominent (larger font, bold, accent color, with red highlighting for negative values).

### 3.3 Responsive Behavior

The primary viewport is a **24″ monitor at 1920×1080 or higher**. The grid is designed to maximize horizontal space at this resolution. Mobile responsiveness is deferred to Phase 6, but the layout should not break at laptop resolutions (1366×768). At smaller viewports, the grid reduces the number of visible periods and the column sizing adapts per the tier system.

---

## 4. Budget Grid — Primary View

### 4.1 Grid Structure

The grid is an HTML table styled with Bootstrap and custom CSS. It is not a spreadsheet component or third-party datagrid library — it is a purpose-built table enhanced with HTMX for interactivity.

| Element | Behavior |
|---|---|
| **Columns** | Each column is one pay period. Current period is leftmost; future periods extend right. Past periods are accessible via left-arrow navigation but are not shown by default. |
| **Frozen left column** | The leftmost column (transaction names, category headers, section banners, and summary labels) uses `position: sticky; left: 0` with an **opaque background** matching the grid surface color and a **right border** to create a clean edge. This prevents scrolling data from showing through the frozen column. All frozen cells use `z-index: 2` (header uses `z-index: 3`) to layer correctly. |
| **Section banners** | "INCOME" and "EXPENSES" are displayed as full-width banner rows with distinct colored backgrounds (green-tinted for income, rose-tinted for expenses). A **16px spacer row** using the page background color separates the two sections. Banner labels are frozen in the left column. |
| **Category sub-headers** | Within each section, categories (Auto, Home, Family, etc.) appear as sub-header rows with a slightly different background. The category name cell is **frozen** in the left column just like item names — it does not scroll with the data. Period cells in category rows are empty but carry the same background color for visual continuity. |
| **Row hover** | No zebra striping. Row tracking is achieved via **hover highlighting** — when the mouse enters any row, all cells in that row change to the hover background color. This is more effective than zebra striping in dark themes (where alternating row colors often fail WCAG contrast requirements). |
| **Income section** | Income rows appear at the top of the grid, under the green "INCOME" banner. |
| **Expense section** | Expense rows appear below the 16px spacer, under the rose "EXPENSES" banner, grouped by category sub-headers. |
| **Summary rows** | Pinned to the bottom of the viewport (`position: sticky; bottom: 0`). Always visible: Total Income, Total Expenses, Net, and Projected End Balance. Separated from data rows by a spacer and an accent-colored top border. End Balance row is visually emphasized (larger font, bold, accent color). |

### 4.2 Column Sizing Tiers

Column width adapts to the number of visible periods. The default landing view shows **6 periods** (the user's typical focus range). The user can widen the view to scan up to 15+ periods for long-range balance projection.

| Periods Visible | Column Tier | Cell Content |
|---|---|---|
| 1–6 | Wide | Amount, status badge (✓ or CC), and inline action buttons (mark done, mark credit). Actual amount shown alongside strikethrough estimate when they differ. |
| 7–13 | Medium | Amount and status badge only. Click or hover for action buttons. |
| 14+ | Compact | Amount only. Status indicated by badge. Click or hover to expand detail. |

### 4.3 Default Landing View

On load, the grid displays **6 pay periods** starting from the current period. This is configurable in user settings (`grid_default_periods`). The 6-period default balances the need to see upcoming obligations against column readability at the Wide tier. Quick-select buttons allow expanding to 10 periods, 6 months, 1 year, or the full 2-year horizon as needed for long-range scanning.

Previous (completed) pay periods are not shown by default. The user can navigate to them via left-arrow controls, but the primary view always starts at the current period.

### 4.4 Anchor Balance Display

The anchor balance is the single most important number in the app. It is displayed in a **large, prominent position above the grid**, left-aligned, using JetBrains Mono at approximately 28px bold. It is labeled "Checking Balance" with the anchored pay period date below it ("as of Feb 14, 2026").

Clicking the balance transforms it into an inline input field (HTMX swap). On Enter or blur, the new value saves and all visible balances recalculate. Escape cancels the edit. The field supports keyboard entry: click or Tab to focus, type the new amount, Enter to save.

### 4.5 Date Range Controls

Positioned in the top bar above the grid, alongside the anchor balance:

- **Quick-select buttons:** 3P, 6P (default), 10P, 6M, 1Y, 2Y. Rendered as a button group with the active selection highlighted in the accent color.
- **Left/right arrow buttons** shift the visible window by one period without changing the range size. Keyboard shortcut: `Ctrl+Left` / `Ctrl+Right`.
- **Current period date range** is displayed as a label between the arrows.

---

## 5. Cell Interaction & Editing

### 5.1 Inline Cell Editing

Clicking a cell transforms it into an **in-place input field**, mirroring the feel of editing a spreadsheet cell. The cell's border gains a focus ring in the primary accent color. The input is pre-populated with the current value (including decimals) and auto-selected for immediate overwrite.

**Edit lifecycle:**

1. Click cell (or Tab into it) → cell transforms to input via `hx-get`
2. User types new value
3. **Enter** or **Tab** → save via `hx-patch`, server returns updated cell fragment, HTMX swaps it in, `HX-Trigger` fires balance recalculation
4. **Escape** → cancel, revert to display mode without saving
5. Brief green flash animation (150ms fade) on the saved cell confirms the save

### 5.2 Status Actions

Status changes are available as **single-click icon buttons** within each cell (at the Wide column tier) or via a small popover (at Medium/Compact tiers). Each action is a single HTMX POST that returns the updated cell fragment.

| Action | Icon | Behavior | Confirmation |
|---|---|---|---|
| Mark Done | Checkmark (✓) | Sets status to done; prompts for actual amount if different from estimate | None — instant, undoable |
| Mark Received | Checkmark (✓) | Sets income status to received | None — instant, undoable |
| Mark Credit | CC badge | Sets status to credit; auto-creates payback in next period | None — instant, undoable |
| Unmark Credit | Undo icon | Reverts to projected; deletes auto-generated payback | None — instant |
| Carry Forward | Forward arrow | Moves all projected items from a past period to current | None — undoable |

### 5.3 Save Feedback

After any edit saves, the affected cell displays a **brief flash animation**: the cell background pulses to a muted accent color for 150ms then fades back to normal. This provides non-intrusive visual confirmation without toast notifications or modal dialogs. When balances recalculate (triggered by the `HX-Trigger` header), the summary row cells also flash briefly to draw attention to the updated projections.

### 5.4 Confirmation Policy

| Tier | Actions | UX Pattern |
|---|---|---|
| Reversible | Mark done/credit, carry forward, amount edits, status changes | No confirmation. Instant action with flash feedback. Undo available. |
| Significant | Delete template, deactivate recurrence rule, regenerate with override conflicts | Confirmation dialog: describes consequence, requires explicit click to proceed. |
| Irreversible | (Currently none in Phase 1 — soft-delete is used throughout) | Double confirmation (type to confirm) if introduced in future. |

---

## 6. Status Visual Indicators

### 6.1 Cell Status Rendering

Each cell's status is communicated via **small badges appended after the amount**, not by changing the amount's text color. All amounts use the same primary text color for maximum readability. This matches the spreadsheet convention.

| Status | Amount Color | Badge | Cell Appearance |
|---|---|---|---|
| Projected | Primary text (normal) | None | Standard cell, no decoration. The default state needs no indicator. |
| Done | Primary text | Green ✓ | Amount in primary color with green checkmark badge. |
| Received | Primary text | Green ✓ | Same treatment as Done, applied to income rows. |
| Credit | Primary text | Amber CC | Amount in primary color with amber CC badge. |

### 6.2 When Actual Differs from Estimated

When a transaction is marked done/received/credit and the actual amount differs from the estimate, the cell displays information in priority order:

1. **Paid status** — the ✓ or CC badge is the primary signal.
2. **Actual amount** — displayed in the semantic status color (green for done, amber for credit) as the primary number.
3. **Estimated amount** — shown with strikethrough in muted text color, smaller font, to the left of the actual.

Example: ~~500~~ **487** ✓

The difference (savings or overspend) is not displayed in the cell to preserve density. The summary rows capture the net effect.

### 6.3 Balance Warning Indicators

The Projected End Balance row in the summary uses color to signal financial health:

| Condition | Treatment |
|---|---|
| Positive balance | Accent color text (normal) |
| Low balance (below configurable threshold) | Amber/yellow text. Threshold stored in `user_settings`, default $500. |
| Negative balance | Red text (`#E74C3C`) with red background tint (`#3A1A1A` dark / `#FDE8E6` light). Matches the spreadsheet convention. |

---

## 7. Keyboard Navigation

Keyboard navigation is a first-class feature. The grid supports a focused-cell model similar to a spreadsheet. A **subtle focus ring** (2px solid accent color) indicates the active cell.

### 7.1 Grid Navigation Keys

| Key | Action |
|---|---|
| Arrow keys | Move focus between cells (up/down within a column, left/right between periods) |
| Tab | Move focus to next cell (left-to-right, then down). Shift+Tab moves backward. |
| Enter | Open focused cell for editing. While editing, Enter saves and moves focus down. |
| Escape | Cancel edit (revert to display mode). If not editing, clear focus. |
| Space | Toggle status of focused cell: projected → done (expenses) or projected → received (income). |
| Ctrl+Left / Right | Shift the grid's visible period window left or right by one period. |
| Home | Jump focus to the first cell of the current row. |
| End | Jump focus to the last visible cell of the current row. |

### 7.2 Implementation Notes

Keyboard navigation is implemented in a small custom JavaScript module (`app.js`), not a third-party library. The module tracks the currently focused cell by `[row, column]` coordinates and translates key events into HTMX-triggered swaps or focus changes. Focus state is stored in a JavaScript object and survives HTMX partial swaps via the `htmx:afterSwap` event handler that restores focus to the correct cell after a swap completes.

---

## 8. Secondary Pages

### 8.1 Template Management

A full-page list view showing all transaction templates with their recurrence rules. Styled as a Bootstrap table with sortable columns (name, category, amount, pattern, active/inactive). A top-right button opens the create form. Each row has edit and deactivate action links.

The edit page is a standard Bootstrap form with fields for name, category selector, default amount, recurrence pattern selector, and recurrence parameters. Saving triggers regeneration with override prompts (displayed as a confirmation modal listing affected transactions).

### 8.2 Category Management

A simple page with a list of categories grouped by group name, each showing its item names. Inline add/edit via HTMX (click to edit a name, Enter to save). Manual sort-order numbers for Phase 1; drag-and-drop reordering is a future enhancement.

### 8.3 Settings Page

A single-page form with user-configurable values: default grid periods, default inflation rate, low balance warning threshold, theme preference. Standard Bootstrap form layout. Save button at the bottom.

### 8.4 Login Page

Minimal, centered card layout with the Shekel wordmark above the form. Email and password fields, a "Remember me" checkbox, and a login button. No registration link in Phase 1 (single seeded user). Error messages appear inline above the form. The page respects the stored theme preference or defaults to dark.

### 8.5 Paycheck Breakdown View (Phase 2)

Accessible from the salary income row in the grid. A detail page showing the full calculation pipeline as a read-only vertical ledger: annual salary, raises applied, gross biweekly, pre-tax deductions (each line), taxable income, taxes (federal, state, SS, Medicare each line), post-tax deductions, and the final net pay highlighted prominently with clear section dividers.

### 8.6 Scenario Comparison View (Phase 3)

Two grids displayed side by side (or overlaid with tabs). Differences between scenarios are highlighted: green background for periods where the selected scenario has a better balance, red for worse. A summary card at the top shows the total divergence at 6 months, 1 year, and 2 years.

---

## 9. HTMX Interaction Patterns

All interactive behavior in the grid is powered by HTMX partial swaps. The app returns **HTML fragments** (not JSON) from the server. No client-side rendering framework is used.

### 9.1 Core HTMX Patterns

| Action | HTMX Trigger | Server Response |
|---|---|---|
| Click cell to edit | `hx-get` on cell click | Returns `_transaction_edit.html` (input field). `hx-swap="innerHTML"`. |
| Save cell edit | `hx-patch` on Enter/blur | Returns updated `_transaction_cell.html`. `HX-Trigger` header fires `balance-recalc` event. |
| Status change | `hx-post` on icon click | Returns updated cell + `HX-Trigger` for `balance-recalc`. |
| Balance recalculate | Listens for `balance-recalc` event | `hx-get` on `_balance_row.html` target. Refreshes all summary rows. |
| Anchor balance edit | `hx-get` / `hx-patch` | Returns `_anchor_edit.html` then updated anchor display + full `balance-recalc` trigger. |
| Carry forward | `hx-post` on button | Returns refreshed source and target period columns. `HX-Trigger` fires `balance-recalc`. |

### 9.2 Swap Strategy

- Cell-level swaps use `hx-swap="innerHTML"` to replace only the cell content, preserving the table cell element and its CSS classes.
- Column-level swaps (carry forward, period navigation) use `hx-swap="outerHTML"` on the column container element.
- The balance summary row uses `hx-swap="outerHTML"` on the entire summary `<tr>` element and listens to a custom `balance-recalc` event.
- HTMX responses include the `HX-Trigger: balance-recalc` header whenever a transaction amount or status changes, ensuring summary rows always reflect the latest state.

---

## 10. Accessibility Baseline

While Shekel is a single-user personal tool, basic accessibility standards improve usability and establish good habits for multi-user support.

- Color contrast ratios meet WCAG 2.1 AA (4.5:1 for normal text, 3:1 for large text) in both themes.
- All interactive elements are focusable and operable via keyboard.
- ARIA labels on status icons and action buttons (e.g., `aria-label="Mark as done"`).
- Focus management after HTMX swaps: focus is restored to the logically correct element after partial page updates.
- Semantic HTML: `<table>`, `<thead>`, `<tbody>`, `<th scope="col">`, `<th scope="row">` for the grid.
- Status communicated via badges and icons — not by color alone. Every status has both a color and a text/icon indicator.

---

## 11. CSS Architecture

### 11.1 Bootstrap Customization

The app uses Bootstrap 5 loaded via CDN. A custom stylesheet (`app.css`) overrides Bootstrap's default variables for colors, fonts, and spacing to match the Shekel design system. Theme switching is achieved by toggling a `data-bs-theme` attribute on the `<html>` element, with CSS custom properties handling all color differences between themes.

### 11.2 Custom CSS Scope

Custom CSS is minimal and targeted. It covers:

- Grid-specific styles: frozen column (`position: sticky`), sticky summary rows, cell sizing, hover highlight, focus ring
- Section banner styles: income (green-tinted) and expense (rose-tinted) backgrounds
- Category sub-header styles
- Status badge classes: `.badge-done`, `.badge-credit`
- Flash animation keyframes for save feedback
- Theme variable overrides (CSS custom properties for all palette colors)
- Monospace font application for financial figures
- Anchor balance styling
- Right border on frozen column cells

### 11.3 File Organization

All custom CSS lives in a single file: `app/static/css/app.css`. No CSS preprocessor (Sass, Less) is used in Phase 1 to maintain the zero-build-pipeline philosophy. If the file exceeds 500 lines, it can be split into logical partials imported via `@import`.

---

## 12. UI/UX Phase Roadmap

| Phase | UI/UX Deliverables |
|---|---|
| **Phase 1** | Custom Bootstrap theme (dark + light with toggle), grid layout with frozen column and sticky summary rows, inline cell editing with flash feedback, status badge indicators, section banners (income/expense), anchor balance display, date range controls, keyboard navigation, row hover highlighting, login page, template/category/settings pages |
| **Phase 2** | Paycheck breakdown view (vertical ledger layout), salary projection view with Chart.js line chart, salary profile forms |
| **Phase 3** | Scenario switcher in grid header, side-by-side comparison layout, balance diff highlighting (green/red overlay) |
| **Phase 4** | Accounts dashboard with savings balance cards, transfer form, savings goal progress bars |
| **Phase 5** | Chart.js integration: balance-over-time line chart, category spending bar chart, budget vs. actuals, scenario comparison overlay, net pay trajectory |
| **Phase 6** | Mobile-responsive grid (collapse to single-period view on small screens), MFA setup page with QR code, registration form, CSV export UI, logo and full brand identity |
| **Phase 7** | Smart estimate suggestion badges on cells (accept/dismiss), inflation indicator icons on adjusted amounts |
| **Phase 8** | Notification bell icon + dropdown in header, notification settings toggles, email digest preferences |

---

## 13. Open Questions & Future Considerations

- **Logo design:** To be created before or during Phase 6. Consider a simple geometric icon (coin, scale) alongside the Shekel wordmark.
- **Drag-and-drop row reordering:** Useful for manual `sort_order` on categories and templates. Evaluate SortableJS or native HTML drag-and-drop.
- **Print stylesheet:** Should the grid be printable? Low priority but a future consideration.
- **Touch interaction for mobile (Phase 6):** Long-press for cell edit, swipe for period navigation.
- **Animation library:** CSS-only animations are sufficient for Phase 1 flash effects. Evaluate a lightweight library if needed later.
- **Configurable low-balance threshold:** Included in `user_settings`. Default $500. Display amber text when projected end balance drops below threshold.

---

## 14. Change Log

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-02-22 | Initial UI/UX design document. Includes design philosophy, color palette (v3 — steel blue accent, cool dark base, saturated semantics, spreadsheet-informed text handling), layout and navigation, budget grid specification (frozen column with opaque backgrounds, section banners, row hover, no zebra striping), cell interaction and editing patterns, status badge system, keyboard navigation, secondary pages, HTMX patterns, accessibility baseline, CSS architecture, phase roadmap. |
