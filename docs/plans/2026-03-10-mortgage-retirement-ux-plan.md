# Mortgage Payoff & Retirement Readiness UX Improvements

## Overview

This plan addresses two deferred UI/UX audit findings:

- **4G: Mortgage Payoff Scenarios** -- The payoff calculator is buried below 3-4 sections on a long page, requiring significant scrolling to reach. The comparison chart is disconnected from the main chart.
- **4H: Retirement Readiness Review** -- The gap analysis ties together data from multiple sources (salary, pensions, accounts, settings) but none of the relationships are explained. Users see numbers with no indication of where they come from.

**Scope:** 3 template files modified. Zero route, service, model, JS, or CSS changes. No new files created. All existing tests pass unchanged.

**Risk level:** Low. Template-only changes with no impact on data flow, HTMX interactions, or business logic.

---

## Guiding Principles

1. **No route changes.** All data flow stays the same. No new endpoints, no modified responses.
2. **No service changes.** No business logic modifications.
3. **No JS changes.** Existing chart and slider scripts work unchanged within the new layout.
4. **Preserve all HTMX interactions.** Target element IDs (`#escrow-list`, `#rate-history`, `#payoff-results`) remain intact within their respective containers.
5. **Match existing patterns.** Tooltips follow the same `<i class="bi bi-info-circle text-muted" title="...">` pattern used on `mortgage/dashboard.html` (P&I) and `salary/list.html` (net pay).
6. **Test gate is mandatory.** All 784+ existing tests must pass after changes.

---

## Change 1: Mortgage Dashboard Tabbed Layout (Audit 4G)

### Problem

The mortgage dashboard (`/accounts/<id>/mortgage`) stacks 5 sections vertically:

1. Loan Summary + Loan Parameters (two-column card row)
2. Escrow Components card
3. Rate History card (ARM only)
4. Payoff Calculator card (with extra payment / target date sub-tabs)
5. Balance Over Time chart

The payoff calculator -- the most interactive feature -- is buried below all other sections. A user specifically looking for payoff scenarios must scroll past the entire page to reach it.

### Solution

Convert the page body into Bootstrap 5 `nav-tabs` with tab panes. The breadcrumbs and page header (title + back button) remain above the tabs. Each section becomes its own tab, accessible with one click.

### Tab Structure

| Tab Label | ID | Content | Default Active |
|-----------|-----|---------|----------------|
| Overview | `tab-overview` | Loan Summary card + Loan Parameters card (existing two-column layout) + Balance Over Time chart canvas | Yes |
| Escrow | `tab-escrow` | Escrow Components card with HTMX add/delete (existing `_escrow_list.html` partial) | No |
| Rate History | `tab-rates` | Rate History card with HTMX add (existing `_rate_history.html` partial). **Tab hidden entirely when `params.is_arm` is false.** | No |
| Payoff Calculator | `tab-payoff` | Payoff Calculator card with extra payment / target date sub-tabs + `#payoff-results` container + comparison chart canvas | No |

### Template Structure

```
app/templates/mortgage/dashboard.html
├── Breadcrumbs (unchanged, above tabs)
├── Page header with back button (unchanged, above tabs)
├── <ul class="nav nav-tabs">
│   ├── Overview tab link (active)
│   ├── Escrow tab link
│   ├── Rate History tab link (conditional: params.is_arm)
│   └── Payoff Calculator tab link
├── <div class="tab-content">
│   ├── #tab-overview (active)
│   │   ├── Two-column: Loan Summary + Loan Parameters
│   │   └── Balance Over Time chart canvas
│   ├── #tab-escrow
│   │   └── {% include "mortgage/_escrow_list.html" %}
│   ├── #tab-rates (conditional: params.is_arm)
│   │   └── {% include "mortgage/_rate_history.html" %}
│   └── #tab-payoff
│       ├── Extra Payment sub-tab form (HTMX)
│       ├── Target Date sub-tab form (HTMX)
│       └── #payoff-results container
└── Script block (unchanged)
```

### HTMX Compatibility

All existing HTMX interactions work unchanged within tab panes:

| Interaction | Target Element | Location in Tabs |
|-------------|---------------|------------------|
| Escrow add/delete | `#escrow-list` | Inside Escrow tab pane |
| Rate history add | `#rate-history` | Inside Rate History tab pane |
| Payoff calculate | `#payoff-results` | Inside Payoff Calculator tab pane |

HTMX targets are element IDs, not DOM paths. Moving them into tab panes does not affect targeting.

### Chart.js Compatibility

- **Overview tab (active by default):** The main amortization chart renders on `DOMContentLoaded` as it does today. The tab is visible on page load, so Chart.js has no rendering issues.
- **Payoff Calculator tab:** The comparison chart only appears after an HTMX swap (user submits the calculator form). The user must be on the Payoff tab to submit the form, so the tab is visible when the chart renders. The existing `htmx:afterSwap` handler in `payoff_chart.js` works unchanged.
- **Tab switching:** Chart.js with `responsive: true` handles resize automatically when the user returns to a tab. No special handling needed.

### File

- `app/templates/mortgage/dashboard.html` -- Restructure body into tab nav + tab panes. Content moves into panes without modification. The Escrow tab pane wraps the existing `_escrow_list.html` include. The Rate History tab pane wraps the existing `_rate_history.html` include (with the same `{% if params.is_arm %}` conditional on both the tab link and pane).

### What Does NOT Change

- `app/routes/mortgage.py` -- All route handlers stay the same
- `app/services/amortization_engine.py` -- No logic changes
- `app/services/escrow_calculator.py` -- No logic changes
- `app/static/js/payoff_chart.js` -- No JS changes
- `app/templates/mortgage/_payoff_results.html` -- No partial changes
- `app/templates/mortgage/_escrow_list.html` -- No partial changes
- `app/templates/mortgage/_rate_history.html` -- No partial changes

---

## Change 2: Retirement Gap Analysis Tooltips (Audit 4H)

### Problem

The retirement dashboard gap analysis table shows calculated numbers derived from multiple data sources (salary profiles, pension profiles, account balances, user settings, growth projections), but none of the relationships are explained. A user seeing "Pre-Retirement Net Monthly: $3,450" has no idea it comes from their salary profile's net pay calculation.

### Solution

Add an `<i class="bi bi-info-circle text-muted" title="..."></i>` icon next to each label in the gap analysis table, pension details card, and retirement accounts table. This matches the existing pattern used on `mortgage/dashboard.html` (P&I tooltip) and `salary/list.html` (net pay tooltip).

### Gap Analysis Table Tooltips

File: `app/templates/retirement/_gap_analysis.html`

| Row Label | Tooltip Text |
|-----------|-------------|
| Pre-Retirement Net Monthly | "Based on your salary profile's net biweekly pay, projected to retirement with raises applied (net biweekly × 26 ÷ 12)." |
| Monthly Pension | "Total monthly benefit from all active pension profiles, calculated from years of service, benefit multiplier, and highest consecutive salary average." |
| After-Tax Monthly Pension | "Monthly pension reduced by your estimated retirement tax rate, configured in Settings > Retirement." |
| Monthly Income Gap | "The monthly shortfall between your pre-retirement income and pension income. Your savings must cover this gap." |
| Required Savings at X% SWR | "Total savings needed to generate enough withdrawal income to cover the gap. Formula: (monthly gap × 12) ÷ safe withdrawal rate." |
| Projected Retirement Savings | "Sum of all retirement and investment account balances projected to your planned retirement date, including contributions and growth." |
| Surplus / Shortfall | "Projected savings minus required savings. Positive (green) means on track; negative (red) means a shortfall." |
| After-Tax Projected Savings | "Traditional (pre-tax) account balances reduced by your estimated retirement tax rate. Roth balances are not taxed." |
| After-Tax Surplus / Shortfall | "After-tax projected savings minus required savings." |

### Pension Details Tooltips

File: `app/templates/retirement/dashboard.html` (pension benefit card)

| Row Label | Tooltip Text |
|-----------|-------------|
| Years of Service | "Calculated from your hire date to planned retirement date." |
| High Salary Average | "Average of your highest consecutive salary years, determined by the pension's high-years window." |
| Annual Benefit | "Benefit multiplier × years of service × high salary average." |
| Monthly Benefit | "Annual benefit divided by 12." |

### Accounts Table Header Tooltips

File: `app/templates/retirement/dashboard.html` (retirement accounts table)

| Column Header | Tooltip Text |
|---------------|-------------|
| Current Balance | "Current balance from your account records." |
| Projected at Retirement | "Balance projected to your retirement date using the account's assumed growth rate, your contributions, and employer match." |

### Implementation Pattern

Each tooltip follows the existing codebase pattern:

```html
<!-- Before -->
<td class="text-muted">Pre-Retirement Net Monthly</td>

<!-- After -->
<td class="text-muted">Pre-Retirement Net Monthly
  <i class="bi bi-info-circle text-muted" title="Based on your salary profile's net biweekly pay, projected to retirement with raises applied (net biweekly × 26 ÷ 12)."></i>
</td>
```

Native browser `title` attributes are used (not Bootstrap tooltip JS) to match the existing pattern in the codebase and avoid adding JS initialization.

### Files

- `app/templates/retirement/_gap_analysis.html` -- Add info icons with title attributes to 9 table row labels
- `app/templates/retirement/dashboard.html` -- Add info icons to 4 pension detail rows and 2 accounts table column headers

### What Does NOT Change

- `app/routes/retirement.py` -- All route handlers stay the same
- `app/services/retirement_gap_calculator.py` -- No logic changes
- `app/services/pension_calculator.py` -- No logic changes
- `app/services/growth_engine.py` -- No logic changes
- `app/static/js/retirement_gap_chart.js` -- No JS changes
- `app/static/js/chart_slider.js` -- No JS changes

---

## Implementation Sequence

| Step | Change | Files | Risk |
|------|--------|-------|------|
| 1 | Mortgage tabbed layout | `mortgage/dashboard.html` | Low -- template restructure only |
| 2 | Retirement gap analysis tooltips | `retirement/_gap_analysis.html`, `retirement/dashboard.html` | Low -- additive text only |
| 3 | Test gate | Run `pytest` | All 784+ tests must pass |

Steps 1 and 2 are independent and can be done in either order or in parallel.

---

## Test Gate

- [ ] `pytest` passes (all 784+ existing tests)
- [ ] Manual verification: mortgage dashboard renders 3 tabs (4 if ARM) with correct content in each
- [ ] Manual verification: all mortgage HTMX interactions work within tab panes (escrow add/delete, rate history add, payoff calculate)
- [ ] Manual verification: mortgage charts render correctly (overview chart on page load, comparison chart after payoff calculation)
- [ ] Manual verification: ARM rate history tab hidden for fixed-rate mortgages
- [ ] Manual verification: retirement gap analysis table shows info icons on all rows
- [ ] Manual verification: pension details show info icons on all rows
- [ ] Manual verification: accounts table headers show info icons
- [ ] Manual verification: tooltip text appears on hover for all info icons
- [ ] Manual verification: no `url_for` endpoint names were altered
- [ ] Manual verification: no URL paths were changed

---

## Files Summary

| File | Change Type | Description |
|------|------------|-------------|
| `app/templates/mortgage/dashboard.html` | Modified | Restructure body into Bootstrap tab nav + tab panes |
| `app/templates/retirement/_gap_analysis.html` | Modified | Add info-circle icons with title tooltips to 9 row labels |
| `app/templates/retirement/dashboard.html` | Modified | Add info-circle icons to 4 pension rows + 2 account column headers |

**Total: 3 files modified, 0 files created, 0 routes changed, 0 services changed.**
