/**
 * Goal mode toggle -- show/hide fields based on goal type selection.
 *
 * The fixed-mode ID is read from a data attribute on the select element
 * (set server-side from the Jinja global) so this file never hardcodes
 * database IDs.
 *
 * A goal on a DEBT (plan step credit_card:CC-5-5d) is a milestone to get
 * UNDER: when the chosen account's option carries data-is-debt="true", the
 * target is relabelled, may be $0 (ruling R-CC72), the goal is Fixed only
 * (the mode select is pinned and hidden) and carries no manual per-period
 * contribution (ruling R-CC90; the input is disabled so a hidden value is not
 * submitted).  The server's goal door refuses all of it regardless -- this
 * only keeps the form from offering what a save would refuse.
 */
(function() {
  var modeSelect = document.getElementById('goal_mode_id');
  if (!modeSelect) return;

  var fixedModeId = modeSelect.getAttribute('data-fixed-mode-id');
  var fixedFields = document.getElementById('fixed-fields');
  var incomeFields = document.getElementById('income-fields');
  var accountSelect = document.getElementById('account_id');
  var modeGroup = document.getElementById('goal-mode-group');
  var contributionGroup = document.getElementById('contribution-group');
  var contributionInput = document.getElementById('contribution_per_period');
  var targetLabel = document.getElementById('target-amount-label');
  var targetInput = document.getElementById('target_amount');
  var debtHelp = document.getElementById('debt-target-help');

  function toggleMode() {
    // Toggle Bootstrap's `.d-none` (display:none !important) rather than
    // inline style.display: the template hides #income-fields with `d-none`,
    // and an inline style cannot override an !important rule, so the
    // income-relative fields were previously unreachable through the UI.
    var isFixed = modeSelect.value === fixedModeId;
    fixedFields.classList.toggle('d-none', !isFixed);
    incomeFields.classList.toggle('d-none', isFixed);
  }

  function selectedIsDebt() {
    var option = accountSelect.options[accountSelect.selectedIndex];
    return option !== undefined && option.getAttribute('data-is-debt') === 'true';
  }

  function toggleAccountKind() {
    var isDebt = selectedIsDebt();
    if (isDebt) {
      modeSelect.value = fixedModeId;
    }
    modeGroup.classList.toggle('d-none', isDebt);
    contributionGroup.classList.toggle('d-none', isDebt);
    contributionInput.disabled = isDebt;
    debtHelp.classList.toggle('d-none', !isDebt);
    targetLabel.textContent = targetLabel.getAttribute(
      isDebt ? 'data-debt-label' : 'data-savings-label'
    );
    targetInput.setAttribute('min', isDebt ? '0' : '0.01');
    toggleMode();
  }

  modeSelect.addEventListener('change', toggleMode);
  accountSelect.addEventListener('change', toggleAccountKind);

  // Set initial state on page load (for edit mode).
  toggleAccountKind();
})();
