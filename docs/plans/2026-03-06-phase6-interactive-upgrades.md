# Phase 6 Interactive Upgrades — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete Phase 6 by wiring `chart_slider.js` into the mortgage payoff calculator (U1), investment growth chart (U2), and retirement gap analysis (U3).

**Architecture:** Each upgrade adds range slider(s) to an existing dashboard that trigger HTMX fragment swaps via debounced `slider-changed` events. U1 reuses the existing POST endpoint. U2 and U3 add new GET fragment endpoints with extracted projection logic. Two inline chart JS files (`growth_chart.js`, `retirement_gap_chart.js`) are refactored from IIFEs to named render functions so HTMX-swapped content can re-trigger chart creation.

**Tech Stack:** Flask, Jinja2, HTMX, Chart.js, `chart_slider.js` (already built), `ShekelChart.create()`

**Test count before:** 763 tests passing.

---

## Task 1: U1 — Mortgage Payoff Calculator Slider

The simplest upgrade. The existing `hx-post` form and `_payoff_results.html` fragment need zero backend changes. We add a range slider synced to the text input and have HTMX auto-submit the form on slider change.

### Files

- Modify: `app/templates/mortgage/dashboard.html:163-183,222-226`
- Test: `tests/test_routes/test_mortgage.py` (append new class)

### Step 1: Write the failing test

Append to `tests/test_routes/test_mortgage.py`:

```python
class TestPayoffSlider:
    """Tests for the payoff calculator slider (U1)."""

    def test_dashboard_has_extra_payment_slider(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage dashboard renders a range slider for extra payment."""
        from app.models.mortgage_params import MortgageParams

        mortgage_type = db.session.query(AccountType).filter_by(name="mortgage").one()
        account = Account(
            user_id=seed_user["user"].id,
            account_type_id=mortgage_type.id,
            name="Home Mortgage",
            current_anchor_balance=Decimal("250000.00"),
        )
        db.session.add(account)
        db.session.flush()

        params = MortgageParams(
            account_id=account.id,
            original_principal=Decimal("300000.00"),
            current_principal=Decimal("250000.00"),
            interest_rate=Decimal("0.06500"),
            term_months=360,
            origination_date=date(2023, 1, 1),
            payment_day=1,
        )
        db.session.add(params)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/mortgage")
        assert resp.status_code == 200
        assert b'data-slider-group="payoff"' in resp.data
        assert b'type="range"' in resp.data
        assert b'chart_slider.js' in resp.data
```

Add `from app.models.ref import AccountType` to the file's imports if not already present.

### Step 2: Run test to verify it fails

```bash
pytest tests/test_routes/test_mortgage.py::TestPayoffSlider -v
```

Expected: FAIL — no `data-slider-group` in response.

### Step 3: Implement the template changes

Edit `app/templates/mortgage/dashboard.html`.

**Lines 163-183 — Extra Payment tab form:** Replace with:

```html
      <div class="tab-pane fade show active" id="extra-tab" role="tabpanel">
        <form id="payoff-form"
              hx-post="{{ url_for('mortgage.payoff_calculate', account_id=account.id) }}"
              hx-target="#payoff-results" hx-swap="innerHTML"
              hx-trigger="submit, slider-changed">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <input type="hidden" name="mode" value="extra_payment">
          <div class="row align-items-end">
            <div class="col-md-6">
              <label for="extra_monthly" class="form-label">Extra Monthly Payment</label>
              <div class="input-group">
                <span class="input-group-text">$</span>
                <input type="number" class="form-control" id="extra_monthly" name="extra_monthly"
                       value="200" step="1" min="0"
                       data-slider-group="payoff">
              </div>
              <input type="range" class="form-range mt-2" id="extra_monthly_slider"
                     min="0" max="2000" step="25" value="200"
                     data-slider-group="payoff"
                     data-slider-target="payoff-form"
                     data-slider-debounce="250">
              <div class="d-flex justify-content-between">
                <small class="text-muted">$0</small>
                <small class="text-muted">$2,000</small>
              </div>
            </div>
            <div class="col-md-6">
              <button type="submit" class="btn btn-primary btn-sm">
                <i class="bi bi-calculator"></i> Calculate
              </button>
            </div>
          </div>
        </form>
      </div>
```

**Lines 222-226 — scripts block:** Add `chart_slider.js`:

```html
{% block scripts %}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<script src="{{ url_for('static', filename='js/chart_theme.js') }}"></script>
<script src="{{ url_for('static', filename='js/payoff_chart.js') }}"></script>
<script src="{{ url_for('static', filename='js/chart_slider.js') }}"></script>
{% endblock %}
```

### Step 4: Run test to verify it passes

```bash
pytest tests/test_routes/test_mortgage.py::TestPayoffSlider -v
```

Expected: PASS.

### Step 5: Run full mortgage test suite

```bash
pytest tests/test_routes/test_mortgage.py -v
```

Expected: All existing tests still pass (no backend changes were made).

### Step 6: Commit

```bash
git add tests/test_routes/test_mortgage.py app/templates/mortgage/dashboard.html
git commit -m "feat: add extra payment slider to mortgage payoff calculator (U1)"
```

---

## Task 2: U2 — Investment Growth Horizon Slider

This requires: (a) refactoring `growth_chart.js` from an IIFE to a named function, (b) a new HTMX fragment template, (c) a new route endpoint, and (d) slider controls in the dashboard.

### Files

- Modify: `app/static/js/growth_chart.js`
- Create: `app/templates/investment/_growth_chart.html`
- Modify: `app/routes/investment.py` (add `growth_chart` endpoint)
- Modify: `app/templates/investment/dashboard.html`
- Test: `tests/test_routes/test_investment.py` (append new class)

### Step 1: Refactor `growth_chart.js`

Convert the IIFE to a named `renderGrowthChart()` function with HTMX afterSwap support.

Replace the entire file `app/static/js/growth_chart.js` with:

```javascript
'use strict';

/**
 * Shekel Budget App — Investment Growth Chart
 *
 * Renders a Chart.js line chart showing projected balance over time
 * with contributions overlaid. Reads data from data-* attributes
 * on the canvas element (CSP-compliant).
 * Uses ShekelChart.create() for consistent theming.
 *
 * @param {string} [canvasId='growthChart'] - The canvas element ID.
 */
function renderGrowthChart(canvasId) {
  canvasId = canvasId || 'growthChart';
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.dataset.labels || '[]');
  var balances = JSON.parse(canvas.dataset.balances || '[]').map(Number);
  var contributions = JSON.parse(canvas.dataset.contributions || '[]').map(Number);

  if (labels.length === 0) return;

  ShekelChart.create(canvasId, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Projected Balance',
          data: balances,
          borderColor: ShekelChart.getColor(0),
          backgroundColor: ShekelChart.getColor(0) + '1A',
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 2,
        },
        {
          label: 'Contributions Only',
          data: contributions,
          borderColor: ShekelChart.getColor(1),
          borderDash: [5, 5],
          fill: false,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 1.5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        tooltip: {
          callbacks: {
            label: function (ctx) {
              return ctx.dataset.label + ': $' + ctx.parsed.y.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              });
            },
          },
        },
        legend: { position: 'top' },
      },
      scales: {
        x: {
          ticks: { maxTicksLimit: 12 },
        },
        y: {
          ticks: {
            callback: function (v) {
              return '$' + v.toLocaleString();
            },
          },
        },
      },
    },
  });
}

// Auto-initialize on page load.
document.addEventListener('DOMContentLoaded', function () {
  renderGrowthChart();
});

// Re-render after HTMX swaps.
document.addEventListener('htmx:afterSwap', function () {
  if (document.getElementById('growthChart')) {
    renderGrowthChart();
  }
});
```

### Step 2: Write the failing tests

Append to `tests/test_routes/test_investment.py`:

```python
class TestGrowthChartFragment:
    """Tests for the investment growth chart HTMX fragment (U2)."""

    def test_growth_chart_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET without HX-Request header redirects to dashboard."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment/growth-chart")
        assert resp.status_code == 302

    def test_growth_chart_empty_without_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Returns empty state when no investment params exist."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"No projection data" in resp.data

    def test_growth_chart_with_data(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Returns canvas element when projection data exists."""
        acct = _create_investment_account(seed_user, db.session)
        acct.current_anchor_period_id = seed_periods[0].id
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"growthChart" in resp.data

    def test_growth_chart_idor(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Other user's account returns 404."""
        other_user = _create_other_user(db.session)
        acct_type = db.session.query(AccountType).filter_by(name="401k").one()
        other_acct = Account(
            user_id=other_user.id,
            account_type_id=acct_type.id,
            name="Other 401k",
            current_anchor_balance=Decimal("10000.00"),
        )
        db.session.add(other_acct)
        db.session.commit()
        resp = auth_client.get(
            f"/accounts/{other_acct.id}/investment/growth-chart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
```

### Step 3: Run tests to verify they fail

```bash
pytest tests/test_routes/test_investment.py::TestGrowthChartFragment -v
```

Expected: FAIL — route does not exist.

### Step 4: Create the fragment template

Create `app/templates/investment/_growth_chart.html`:

```html
{% if chart_labels %}
<canvas id="growthChart"
        data-labels='{{ chart_labels|tojson }}'
        data-balances='{{ chart_balances|tojson }}'
        data-contributions='{{ chart_contributions|tojson }}'
        style="max-height: 350px;">
</canvas>
{% else %}
<div class="text-center py-4 text-muted">
  <i class="bi bi-graph-up fs-1"></i>
  <p class="mt-2">No projection data available.</p>
</div>
{% endif %}
```

### Step 5: Add the route endpoint

Add to `app/routes/investment.py` — new imports at top (add `date` to the existing datetime import, add `request` to the Flask import if not already there):

```python
from app.services import growth_engine, pay_period_service, paycheck_calculator
```

`growth_engine` is already imported. Add this route after the `dashboard` function:

```python
@investment_bp.route("/accounts/<int:account_id>/investment/growth-chart")
@login_required
def growth_chart(account_id):
    """HTMX fragment: growth projection chart with adjustable horizon."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("investment.dashboard", account_id=account_id))

    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        return "", 404

    params = (
        db.session.query(InvestmentParams)
        .filter_by(account_id=account_id)
        .first()
    )
    empty = {"chart_labels": [], "chart_balances": [], "chart_contributions": []}

    if not params:
        return render_template("investment/_growth_chart.html", **empty)

    horizon_years = request.args.get("horizon_years", type=int, default=2)
    horizon_years = max(1, min(horizon_years, 40))

    current_balance = account.current_anchor_balance or Decimal("0.00")

    # Generate synthetic future periods for the requested horizon.
    from datetime import timedelta
    end_date = date.today() + timedelta(days=horizon_years * 365)
    periods = growth_engine.generate_projection_periods(
        start_date=date.today(),
        end_date=end_date,
    )

    if not periods:
        return render_template("investment/_growth_chart.html", **empty)

    # Load contribution inputs.
    all_periods = pay_period_service.get_all_periods(current_user.id)
    current_period = pay_period_service.get_current_period(current_user.id)

    salary_gross_biweekly = Decimal("0")
    active_profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .first()
    )
    if active_profile:
        salary_gross_biweekly = (
            Decimal(str(active_profile.annual_salary))
            / (active_profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))

    deductions = (
        db.session.query(PaycheckDeduction)
        .join(SalaryProfile)
        .filter(
            SalaryProfile.user_id == current_user.id,
            SalaryProfile.is_active.is_(True),
            PaycheckDeduction.target_account_id == account_id,
            PaycheckDeduction.is_active.is_(True),
        )
        .all()
    )

    adapted_deductions = []
    for ded in deductions:
        profile = ded.salary_profile
        adapted_deductions.append(type("D", (), {
            "amount": ded.amount,
            "calc_method_name": ded.calc_method.name if ded.calc_method else "flat",
            "annual_salary": profile.annual_salary,
            "pay_periods_per_year": profile.pay_periods_per_year or 26,
        })())

    period_ids = [p.id for p in all_periods]
    acct_transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.to_account_id == account_id,
            Transfer.pay_period_id.in_(period_ids),
            Transfer.is_deleted.is_(False),
        )
        .all()
    ) if period_ids else []

    inputs = calculate_investment_inputs(
        account_id=account_id,
        investment_params=params,
        deductions=adapted_deductions,
        all_transfers=acct_transfers,
        all_periods=all_periods,
        current_period=current_period,
        salary_gross_biweekly=salary_gross_biweekly,
    )

    projection = growth_engine.project_balance(
        current_balance=current_balance,
        assumed_annual_return=params.assumed_annual_return,
        periods=periods,
        periodic_contribution=inputs.periodic_contribution,
        employer_params=inputs.employer_params,
        annual_contribution_limit=params.annual_contribution_limit,
        ytd_contributions_start=inputs.ytd_contributions,
    )

    period_map = {p.id: p for p in periods}
    chart_labels = []
    chart_balances = []
    chart_contributions = []
    cumulative_contrib = Decimal("0")

    for pb in projection:
        p = period_map.get(pb.period_id)
        if p:
            chart_labels.append(p.start_date.strftime("%b %Y"))
        chart_balances.append(str(pb.end_balance.quantize(Decimal("0.01"))))
        cumulative_contrib += pb.contribution + pb.employer_contribution
        chart_contributions.append(
            str((current_balance + cumulative_contrib).quantize(Decimal("0.01")))
        )

    return render_template(
        "investment/_growth_chart.html",
        chart_labels=chart_labels,
        chart_balances=chart_balances,
        chart_contributions=chart_contributions,
    )
```

### Step 6: Run tests to verify they pass

```bash
pytest tests/test_routes/test_investment.py::TestGrowthChartFragment -v
```

Expected: All 4 tests PASS.

### Step 7: Update the dashboard template

Edit `app/templates/investment/dashboard.html`.

**Replace lines 100-115** (Growth Projection Chart card) with:

```html
{# --- Growth Projection Chart --- #}
{% if params %}
<div class="card mb-4">
  <div class="card-header d-flex justify-content-between align-items-center">
    <h6 class="mb-0"><i class="bi bi-graph-up-arrow"></i> Growth Projection</h6>
  </div>
  <div class="card-body">
    <div class="row align-items-center mb-3">
      <div class="col-md-4">
        <label for="horizon_years" class="form-label mb-0">Projection Horizon</label>
      </div>
      <div class="col-md-6">
        <input type="range" class="form-range" id="horizon_slider"
               min="1" max="40" step="1" value="{{ default_horizon }}"
               data-slider-group="horizon"
               data-slider-target="growth-chart-container"
               data-slider-debounce="300">
      </div>
      <div class="col-md-2">
        <div class="input-group input-group-sm">
          <input type="number" class="form-control" id="horizon_years"
                 name="horizon_years" min="1" max="40" step="1"
                 value="{{ default_horizon }}"
                 data-slider-group="horizon">
          <span class="input-group-text">yr</span>
        </div>
      </div>
    </div>
    <div id="growth-chart-container"
         hx-get="{{ url_for('investment.growth_chart', account_id=account.id) }}"
         hx-trigger="slider-changed"
         hx-swap="innerHTML"
         hx-include="#horizon_years">
      {% include "investment/_growth_chart.html" %}
    </div>
  </div>
</div>
{% endif %}
```

**Replace lines 189-195** (scripts block) with:

```html
{% block scripts %}
{% if params %}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<script src="{{ url_for('static', filename='js/chart_theme.js') }}"></script>
<script src="{{ url_for('static', filename='js/growth_chart.js') }}"></script>
<script src="{{ url_for('static', filename='js/chart_slider.js') }}"></script>
{% endif %}
{% endblock %}
```

### Step 8: Update the dashboard route to pass `default_horizon`

In `app/routes/investment.py`, in the `dashboard()` function, compute the default horizon and pass it to the template. Add these lines before the `return render_template(...)` call (around line 170):

```python
    # Default horizon for the growth chart slider.
    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if settings and settings.planned_retirement_date:
        default_horizon = max(1, (settings.planned_retirement_date.year - date.today().year))
    elif all_periods:
        last_period = all_periods[-1]
        default_horizon = max(1, (last_period.end_date.year - date.today().year) + 1)
    else:
        default_horizon = 10
```

Add the import at the top of the file:

```python
from app.models.user import UserSettings
```

And add `default_horizon=default_horizon` to the `render_template()` call.

### Step 9: Run full investment test suite

```bash
pytest tests/test_routes/test_investment.py -v
```

Expected: All existing + new tests pass.

### Step 10: Commit

```bash
git add app/static/js/growth_chart.js \
       app/templates/investment/_growth_chart.html \
       app/templates/investment/dashboard.html \
       app/routes/investment.py \
       tests/test_routes/test_investment.py
git commit -m "feat: add growth projection horizon slider to investment dashboard (U2)"
```

---

## Task 3: U3 — Retirement Gap Sensitivity Sliders

The most complex upgrade. We need to: (a) refactor `retirement_gap_chart.js`, (b) extract gap computation into a helper, (c) implement the `gap_analysis` HTMX endpoint, (d) create a gap fragment template, and (e) add two sliders to the dashboard.

### Files

- Modify: `app/static/js/retirement_gap_chart.js`
- Create: `app/templates/retirement/_gap_analysis.html`
- Modify: `app/routes/retirement.py` (extract helper, implement endpoint)
- Modify: `app/templates/retirement/dashboard.html`
- Test: `tests/test_routes/test_retirement.py` (append new class)

### Step 1: Refactor `retirement_gap_chart.js`

Replace the entire file `app/static/js/retirement_gap_chart.js` with:

```javascript
'use strict';

/**
 * Shekel Budget App — Retirement Income Gap Chart
 *
 * Renders a Chart.js horizontal stacked bar chart showing pension income,
 * investment income, and the remaining gap relative to pre-retirement income.
 * Reads data from data-* attributes on the canvas element (CSP-compliant).
 * Uses ShekelChart.create() for consistent theming.
 *
 * @param {string} [canvasId='gapChart'] - The canvas element ID.
 */
function renderGapChart(canvasId) {
  canvasId = canvasId || 'gapChart';
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var pension = parseFloat(canvas.dataset.pension) || 0;
  var investment = parseFloat(canvas.dataset.investment) || 0;
  var preRetirement = parseFloat(canvas.dataset.preRetirement) || 0;

  if (preRetirement <= 0) return;

  var covered = pension + investment;
  var remaining = Math.max(0, preRetirement - covered);

  ShekelChart.create(canvasId, {
    type: 'bar',
    data: {
      labels: ['Monthly Income'],
      datasets: [
        {
          label: 'Pension',
          data: [pension],
          backgroundColor: ShekelChart.getColor(1),
        },
        {
          label: 'Investment Income (SWR)',
          data: [investment],
          backgroundColor: ShekelChart.getColor(0),
        },
        {
          label: 'Gap',
          data: [remaining],
          backgroundColor: remaining > 0 ? ShekelChart.getColor(6) : ShekelChart.getColor(1),
        },
      ],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            label: function (ctx) {
              return ctx.dataset.label + ': $' + ctx.parsed.x.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              });
            },
          },
        },
        legend: { position: 'top' },
      },
      scales: {
        x: {
          stacked: true,
          ticks: {
            callback: function (v) {
              return '$' + v.toLocaleString();
            },
          },
        },
        y: {
          stacked: true,
          grid: { display: false },
        },
      },
    },
  });
}

// Auto-initialize on page load.
document.addEventListener('DOMContentLoaded', function () {
  renderGapChart();
});

// Re-render after HTMX swaps.
document.addEventListener('htmx:afterSwap', function () {
  if (document.getElementById('gapChart')) {
    renderGapChart();
  }
});
```

### Step 2: Write the failing tests

Append to `tests/test_routes/test_retirement.py`:

```python
class TestGapAnalysisFragment:
    """Tests for the retirement gap analysis HTMX fragment (U3)."""

    def test_gap_redirects_without_htmx(self, auth_client, seed_user, db, seed_periods):
        """GET /retirement/gap without HX-Request redirects to dashboard."""
        resp = auth_client.get("/retirement/gap")
        assert resp.status_code == 302

    def test_gap_returns_fragment(self, auth_client, seed_user, db, seed_periods):
        """GET /retirement/gap with HX-Request returns gap analysis fragment."""
        resp = auth_client.get(
            "/retirement/gap",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"Configure your salary" in resp.data or b"Gap" in resp.data

    def test_gap_with_swr_param(self, auth_client, seed_user, db, seed_periods):
        """SWR slider parameter is accepted and used."""
        profile = _create_salary_profile(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2050, 1, 1)
        settings.safe_withdrawal_rate = Decimal("0.04")
        db.session.commit()

        resp = auth_client.get(
            "/retirement/gap?swr=3.0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        # The fragment should show the 3% rate, not the stored 4%.
        assert b"3%" in resp.data or b"3.0" in resp.data

    def test_gap_with_return_rate_param(self, auth_client, seed_user, db, seed_periods):
        """Return rate slider parameter is accepted."""
        profile = _create_salary_profile(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2050, 1, 1)
        db.session.commit()

        _create_retirement_account(seed_user, db.session, type_name="401k")

        resp = auth_client.get(
            "/retirement/gap?return_rate=10.0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"Gap" in resp.data or b"Surplus" in resp.data
```

### Step 3: Run tests to verify they fail

```bash
pytest tests/test_routes/test_retirement.py::TestGapAnalysisFragment -v
```

Expected: FAIL — current `gap_analysis()` just redirects.

### Step 4: Create the gap analysis fragment template

Create `app/templates/retirement/_gap_analysis.html`:

```html
{# HTMX fragment: gap analysis table + chart #}
{% if gap_analysis %}
<table class="table table-sm mb-0">
  <tbody>
    <tr>
      <td class="text-muted">Projected Pre-Retirement Income</td>
      <td class="text-end font-mono fw-bold">${{ "{:,.2f}".format(gap_analysis.pre_retirement_net_monthly|float) }}</td>
    </tr>
    <tr>
      <td class="text-muted">Projected Monthly Pension</td>
      <td class="text-end font-mono">${{ "{:,.2f}".format(gap_analysis.monthly_pension_income|float) }}</td>
    </tr>
    {% if gap_analysis.after_tax_monthly_pension is not none %}
    <tr>
      <td class="text-muted">After-Tax Monthly Pension</td>
      <td class="text-end font-mono">${{ "{:,.2f}".format(gap_analysis.after_tax_monthly_pension|float) }}</td>
    </tr>
    {% endif %}
    <tr>
      <td class="text-muted">Monthly Income Gap</td>
      <td class="text-end font-mono text-warning">${{ "{:,.2f}".format(gap_analysis.monthly_income_gap|float) }}</td>
    </tr>
    <tr>
      <td class="text-muted">Required Savings ({{ "%.1f"|format(gap_analysis.safe_withdrawal_rate|float * 100) }}% rule)</td>
      <td class="text-end font-mono">${{ "{:,.2f}".format(gap_analysis.required_retirement_savings|float) }}</td>
    </tr>
    <tr>
      <td class="text-muted">Projected Retirement Savings</td>
      <td class="text-end font-mono">${{ "{:,.2f}".format(gap_analysis.projected_total_savings|float) }}</td>
    </tr>
    <tr class="table-active">
      <td class="fw-bold">
        {% if gap_analysis.savings_surplus_or_shortfall >= 0 %}
          Surplus
        {% else %}
          Shortfall
        {% endif %}
      </td>
      <td class="text-end font-mono fw-bold {{ 'text-success' if gap_analysis.savings_surplus_or_shortfall >= 0 else 'text-danger' }}">
        {% if gap_analysis.savings_surplus_or_shortfall >= 0 %}+{% endif %}${{ "{:,.2f}".format(gap_analysis.savings_surplus_or_shortfall|float) }}
      </td>
    </tr>
    {% if gap_analysis.after_tax_projected_savings is not none %}
    <tr>
      <td class="text-muted" colspan="2"><small class="text-muted">After-tax view (estimated)</small></td>
    </tr>
    <tr>
      <td class="text-muted">After-Tax Projected Savings</td>
      <td class="text-end font-mono">${{ "{:,.2f}".format(gap_analysis.after_tax_projected_savings|float) }}</td>
    </tr>
    <tr>
      <td class="text-muted">After-Tax Surplus/Shortfall</td>
      <td class="text-end font-mono {{ 'text-success' if gap_analysis.after_tax_surplus_or_shortfall >= 0 else 'text-danger' }}">
        {% if gap_analysis.after_tax_surplus_or_shortfall >= 0 %}+{% endif %}${{ "{:,.2f}".format(gap_analysis.after_tax_surplus_or_shortfall|float) }}
      </td>
    </tr>
    {% endif %}
  </tbody>
</table>

{% if gap_analysis.pre_retirement_net_monthly > 0 %}
<div class="mt-3">
  <canvas id="gapChart"
          data-pension="{{ chart_data.pension }}"
          data-investment="{{ chart_data.investment_income }}"
          data-gap="{{ chart_data.gap }}"
          data-pre-retirement="{{ chart_data.pre_retirement }}"
          style="max-height: 300px;">
  </canvas>
</div>
{% endif %}
{% else %}
<p class="text-muted mb-0">Configure your salary profile and retirement settings to see the gap analysis.</p>
{% endif %}
```

### Step 5: Extract gap computation helper in retirement.py

Add this helper function to `app/routes/retirement.py`, before the `dashboard()` route:

```python
def _compute_gap_data(user_id, swr_override=None, return_rate_override=None):
    """Compute gap analysis data for the retirement dashboard or HTMX fragment.

    Args:
        user_id: The user's ID.
        swr_override: Optional Decimal safe withdrawal rate from slider.
        return_rate_override: Optional Decimal annual return rate from slider.

    Returns:
        dict with keys: gap_analysis, chart_data, pension_benefit,
                        retirement_account_projections, settings,
                        salary_profiles, pensions.
    """
    settings = (
        db.session.query(UserSettings).filter_by(user_id=user_id).first()
    )

    pensions = (
        db.session.query(PensionProfile)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )
    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )

    # Calculate pension benefit.
    pension_benefit = None
    monthly_pension_income = Decimal("0")
    salary_by_year = None
    for pension in pensions:
        if pension.planned_retirement_date and pension.salary_profile:
            profile = pension.salary_profile
            start_year = date.today().year
            end_year = pension.planned_retirement_date.year
            salary_by_year = pension_calculator.project_salaries_by_year(
                Decimal(str(profile.annual_salary)),
                profile.raises,
                start_year,
                end_year,
            )
            benefit = pension_calculator.calculate_benefit(
                benefit_multiplier=pension.benefit_multiplier,
                consecutive_high_years=pension.consecutive_high_years,
                hire_date=pension.hire_date,
                planned_retirement_date=pension.planned_retirement_date,
                salary_by_year=salary_by_year,
            )
            pension_benefit = benefit
            monthly_pension_income += benefit.monthly_benefit

    # Calculate net biweekly pay.
    all_periods = pay_period_service.get_all_periods(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    net_biweekly = Decimal("0")
    if salary_profiles:
        profile = salary_profiles[0]
        if current_period:
            from app.routes.salary import _load_tax_configs
            tax_configs = _load_tax_configs(user_id, profile)
            breakdown = paycheck_calculator.calculate_paycheck(
                profile, current_period, all_periods, tax_configs,
            )
            net_biweekly = breakdown.net_pay

    # Load retirement/investment accounts and project balances.
    retirement_types = (
        db.session.query(AccountType)
        .filter(AccountType.category.in_(["retirement", "investment"]))
        .all()
    )
    retirement_type_ids = {rt.id for rt in retirement_types}
    type_name_map = {rt.id: rt.name for rt in retirement_types}

    accounts = (
        db.session.query(Account)
        .filter(
            Account.user_id == user_id,
            Account.account_type_id.in_(retirement_type_ids),
            Account.is_active.is_(True),
        )
        .all()
    )

    planned_retirement_date = (
        settings.planned_retirement_date if settings else None
    )

    # Batch-load deductions and transfers.
    retirement_account_projections = []
    account_ids = [a.id for a in accounts]

    deductions_by_account = {}
    if account_ids:
        inv_deductions = (
            db.session.query(PaycheckDeduction)
            .join(SalaryProfile)
            .filter(
                SalaryProfile.user_id == user_id,
                SalaryProfile.is_active.is_(True),
                PaycheckDeduction.target_account_id.in_(account_ids),
                PaycheckDeduction.is_active.is_(True),
            )
            .all()
        )
        for ded in inv_deductions:
            deductions_by_account.setdefault(ded.target_account_id, []).append(ded)

    period_ids = [p.id for p in all_periods]
    all_acct_transfers = []
    if account_ids and period_ids:
        all_acct_transfers = (
            db.session.query(Transfer)
            .filter(
                Transfer.to_account_id.in_(account_ids),
                Transfer.pay_period_id.in_(period_ids),
                Transfer.is_deleted.is_(False),
            )
            .all()
        )

    salary_gross_biweekly = Decimal("0")
    if salary_profiles:
        profile = salary_profiles[0]
        salary_gross_biweekly = (
            Decimal(str(profile.annual_salary))
            / (profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))

    synthetic_periods = []
    if planned_retirement_date:
        synthetic_periods = growth_engine.generate_projection_periods(
            start_date=date.today(),
            end_date=planned_retirement_date,
        )

    for acct in accounts:
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=acct.id)
            .first()
        )
        balance = acct.current_anchor_balance or Decimal("0")
        projected_balance = balance

        if params and synthetic_periods:
            acct_deductions = deductions_by_account.get(acct.id, [])
            adapted_deductions = []
            for ded in acct_deductions:
                ded_profile = ded.salary_profile
                adapted_deductions.append(type("D", (), {
                    "amount": ded.amount,
                    "calc_method_name": ded.calc_method.name if ded.calc_method else "flat",
                    "annual_salary": ded_profile.annual_salary,
                    "pay_periods_per_year": ded_profile.pay_periods_per_year or 26,
                })())

            inputs = calculate_investment_inputs(
                account_id=acct.id,
                investment_params=params,
                deductions=adapted_deductions,
                all_transfers=all_acct_transfers,
                all_periods=all_periods,
                current_period=current_period,
                salary_gross_biweekly=salary_gross_biweekly,
            )

            # Use override return rate if provided, else per-account rate.
            annual_return = (
                return_rate_override
                if return_rate_override is not None
                else params.assumed_annual_return
            )

            proj = growth_engine.project_balance(
                current_balance=balance,
                assumed_annual_return=annual_return,
                periods=synthetic_periods,
                periodic_contribution=inputs.periodic_contribution,
                employer_params=inputs.employer_params,
                annual_contribution_limit=inputs.annual_contribution_limit,
                ytd_contributions_start=inputs.ytd_contributions,
            )
            if proj:
                projected_balance = proj[-1].end_balance

        type_name = type_name_map.get(acct.account_type_id, "")
        retirement_account_projections.append({
            "account": acct,
            "projected_balance": projected_balance,
            "is_traditional": type_name in TRADITIONAL_TYPES,
        })

    # Projected salary for gap comparison.
    gap_net_biweekly = net_biweekly
    if salary_profiles and planned_retirement_date and net_biweekly > 0:
        profile = salary_profiles[0]
        current_gross_biweekly = (
            Decimal(str(profile.annual_salary))
            / (profile.pay_periods_per_year or 26)
        ).quantize(Decimal("0.01"))
        if current_gross_biweekly > 0:
            effective_take_home_rate = net_biweekly / current_gross_biweekly
            if salary_by_year is None:
                salary_by_year = pension_calculator.project_salaries_by_year(
                    Decimal(str(profile.annual_salary)),
                    profile.raises,
                    date.today().year,
                    planned_retirement_date.year,
                )
            if salary_by_year:
                final_salary = salary_by_year[-1][1]
                final_gross_biweekly = (
                    final_salary / (profile.pay_periods_per_year or 26)
                ).quantize(Decimal("0.01"))
                gap_net_biweekly = (
                    final_gross_biweekly * effective_take_home_rate
                ).quantize(Decimal("0.01"))

    # Use override SWR if provided, else from settings.
    swr = (
        swr_override
        if swr_override is not None
        else Decimal(str(settings.safe_withdrawal_rate or "0.04")) if settings else Decimal("0.04")
    )
    tax_rate = (
        Decimal(str(settings.estimated_retirement_tax_rate))
        if settings and settings.estimated_retirement_tax_rate
        else None
    )

    gap_result = retirement_gap_calculator.calculate_gap(
        net_biweekly_pay=gap_net_biweekly,
        monthly_pension_income=monthly_pension_income,
        retirement_account_projections=retirement_account_projections,
        safe_withdrawal_rate=swr,
        planned_retirement_date=planned_retirement_date,
        estimated_tax_rate=tax_rate,
    )

    chart_data = {
        "pension": str(monthly_pension_income),
        "investment_income": str(
            (gap_result.projected_total_savings * swr / 12).quantize(Decimal("0.01"))
        ) if gap_result.projected_total_savings > 0 else "0",
        "gap": str(gap_result.monthly_income_gap),
        "pre_retirement": str(gap_result.pre_retirement_net_monthly),
    }

    return {
        "gap_analysis": gap_result,
        "chart_data": chart_data,
        "pension_benefit": pension_benefit,
        "retirement_account_projections": retirement_account_projections,
        "settings": settings,
        "salary_profiles": salary_profiles,
        "pensions": pensions,
    }
```

### Step 6: Refactor the dashboard route to use the helper

Replace the `dashboard()` function body in `app/routes/retirement.py` with:

```python
@retirement_bp.route("/retirement")
@login_required
def dashboard():
    """Retirement planning dashboard with gap analysis."""
    data = _compute_gap_data(current_user.id)

    # Compute current slider defaults from settings.
    settings = data["settings"]
    current_swr = float(settings.safe_withdrawal_rate or 0.04) * 100 if settings else 4.0
    current_return = 7.0  # Default display value for return rate slider.

    return render_template(
        "retirement/dashboard.html",
        current_swr=current_swr,
        current_return=current_return,
        **data,
    )
```

### Step 7: Implement the gap_analysis endpoint properly

Replace the existing placeholder `gap_analysis()` function:

```python
@retirement_bp.route("/retirement/gap")
@login_required
def gap_analysis():
    """HTMX fragment: recalculate gap analysis with slider overrides."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("retirement.dashboard"))

    swr_override = None
    return_rate_override = None

    swr_param = request.args.get("swr", type=float)
    if swr_param is not None:
        swr_override = Decimal(str(swr_param)) / Decimal("100")

    return_param = request.args.get("return_rate", type=float)
    if return_param is not None:
        return_rate_override = Decimal(str(return_param)) / Decimal("100")

    data = _compute_gap_data(
        current_user.id,
        swr_override=swr_override,
        return_rate_override=return_rate_override,
    )

    return render_template(
        "retirement/_gap_analysis.html",
        gap_analysis=data["gap_analysis"],
        chart_data=data["chart_data"],
    )
```

### Step 8: Run tests to verify they pass

```bash
pytest tests/test_routes/test_retirement.py::TestGapAnalysisFragment -v
```

Expected: All 4 tests PASS.

### Step 9: Update the retirement dashboard template

Edit `app/templates/retirement/dashboard.html`.

**Replace lines 12-98** (Income Gap Summary card + Gap Chart card) with:

```html
{# --- Sensitivity Sliders --- #}
{% if gap_analysis %}
<div class="card mb-4">
  <div class="card-header">
    <h6 class="mb-0"><i class="bi bi-sliders"></i> Sensitivity Analysis</h6>
  </div>
  <div class="card-body">
    <div class="row g-3">
      <div class="col-md-6">
        <label for="swr_input" class="form-label">
          Safe Withdrawal Rate: <span id="swr-display">{{ "%.1f"|format(current_swr) }}%</span>
        </label>
        <input type="range" class="form-range" id="swr_slider"
               min="2.0" max="6.0" step="0.25" value="{{ "%.2f"|format(current_swr) }}"
               data-slider-group="gap-swr"
               data-slider-target="gap-analysis-container"
               data-slider-debounce="300">
        <input type="number" class="gap-param" id="swr_input" name="swr"
               step="0.25" min="2.0" max="6.0"
               value="{{ "%.2f"|format(current_swr) }}"
               data-slider-group="gap-swr"
               style="display: none;">
        <div class="d-flex justify-content-between">
          <small class="text-muted">2.0%</small>
          <small class="text-muted">6.0%</small>
        </div>
      </div>
      <div class="col-md-6">
        <label for="return_input" class="form-label">
          Assumed Annual Return: <span id="return-display">{{ "%.1f"|format(current_return) }}%</span>
        </label>
        <input type="range" class="form-range" id="return_slider"
               min="3.0" max="12.0" step="0.5" value="{{ "%.1f"|format(current_return) }}"
               data-slider-group="gap-return"
               data-slider-target="gap-analysis-container"
               data-slider-debounce="300">
        <input type="number" class="gap-param" id="return_input" name="return_rate"
               step="0.5" min="3.0" max="12.0"
               value="{{ "%.1f"|format(current_return) }}"
               data-slider-group="gap-return"
               style="display: none;">
        <div class="d-flex justify-content-between">
          <small class="text-muted">3.0%</small>
          <small class="text-muted">12.0%</small>
        </div>
      </div>
    </div>
  </div>
</div>
{% endif %}

{# --- Income Gap Analysis (HTMX swappable) --- #}
<div class="card mb-4">
  <div class="card-header">
    <h6 class="mb-0"><i class="bi bi-calculator"></i> Retirement Income Gap Analysis</h6>
  </div>
  <div class="card-body"
       id="gap-analysis-container"
       hx-get="{{ url_for('retirement.gap_analysis') }}"
       hx-trigger="slider-changed"
       hx-swap="innerHTML"
       hx-include=".gap-param">
    {% include "retirement/_gap_analysis.html" %}
  </div>
</div>
```

**Replace lines 230-237** (scripts block) with:

```html
{% block scripts %}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<script src="{{ url_for('static', filename='js/chart_theme.js') }}"></script>
<script src="{{ url_for('static', filename='js/retirement_gap_chart.js') }}"></script>
<script src="{{ url_for('static', filename='js/chart_slider.js') }}"></script>
<script>
  // Update slider display labels on input.
  document.addEventListener('DOMContentLoaded', function() {
    var swrSlider = document.getElementById('swr_slider');
    var swrDisplay = document.getElementById('swr-display');
    var returnSlider = document.getElementById('return_slider');
    var returnDisplay = document.getElementById('return-display');

    if (swrSlider && swrDisplay) {
      swrSlider.addEventListener('input', function() {
        swrDisplay.textContent = parseFloat(swrSlider.value).toFixed(1) + '%';
      });
    }
    if (returnSlider && returnDisplay) {
      returnSlider.addEventListener('input', function() {
        returnDisplay.textContent = parseFloat(returnSlider.value).toFixed(1) + '%';
      });
    }
  });
</script>
{% endblock %}
```

Note: Chart.js and chart_theme.js are now loaded unconditionally (the sliders and gap chart need them whenever gap_analysis data exists). The old conditional `{% if gap_analysis and ... %}` is no longer needed because the scripts block handles the case where there's no gap data gracefully — `renderGapChart()` checks for the canvas element and returns early.

### Step 10: Run full retirement test suite

```bash
pytest tests/test_routes/test_retirement.py -v
```

Expected: All existing + new tests pass.

### Step 11: Commit

```bash
git add app/static/js/retirement_gap_chart.js \
       app/templates/retirement/_gap_analysis.html \
       app/templates/retirement/dashboard.html \
       app/routes/retirement.py \
       tests/test_routes/test_retirement.py
git commit -m "feat: add sensitivity sliders to retirement gap analysis (U3)"
```

---

## Task 4: Final Verification

### Step 1: Run the full test suite

```bash
pytest -v
```

Expected: All 763+ tests pass (original 763 + ~9 new = ~772).

### Step 2: Verify no regressions

Check that these existing views still render correctly:
- `GET /accounts/<id>/mortgage` — dashboard with slider visible
- `GET /accounts/<id>/investment` — dashboard with horizon slider
- `GET /retirement` — dashboard with sensitivity sliders
- `GET /charts` — all 6 charts still load via HTMX

### Step 3: Commit (if any final fixes needed)

```bash
git add -A
git commit -m "chore: final Phase 6 verification pass"
```

---

## Summary of Changes

| File | Action | Upgrade |
|------|--------|---------|
| `app/templates/mortgage/dashboard.html` | Modify | U1 — add range slider + form trigger |
| `tests/test_routes/test_mortgage.py` | Modify | U1 — slider test |
| `app/static/js/growth_chart.js` | Modify | U2 — IIFE → named function |
| `app/templates/investment/_growth_chart.html` | Create | U2 — chart fragment |
| `app/templates/investment/dashboard.html` | Modify | U2 — include fragment + slider |
| `app/routes/investment.py` | Modify | U2 — `growth_chart()` endpoint |
| `tests/test_routes/test_investment.py` | Modify | U2 — fragment tests |
| `app/static/js/retirement_gap_chart.js` | Modify | U3 — IIFE → named function |
| `app/templates/retirement/_gap_analysis.html` | Create | U3 — gap fragment |
| `app/templates/retirement/dashboard.html` | Modify | U3 — include fragment + sliders |
| `app/routes/retirement.py` | Modify | U3 — `_compute_gap_data()` + `gap_analysis()` |
| `tests/test_routes/test_retirement.py` | Modify | U3 — fragment tests |

**New tests:** 9 (1 mortgage + 4 investment + 4 retirement)
**New files:** 2 (`_growth_chart.html`, `_gap_analysis.html`)
**Modified files:** 10
**Backend logic added:** 2 new route endpoints, 1 extracted helper
**Backend logic changed:** 0 existing endpoints modified
