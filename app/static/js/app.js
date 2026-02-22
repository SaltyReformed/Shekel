/**
 * Shekel Budget App — Client-Side JavaScript
 *
 * Minimal JS: HTMX handles most interactivity server-side.
 * This file provides the theme toggle and small UX helpers.
 */

// --- Theme Toggle ---
(function() {
  var saved = localStorage.getItem('shekel-theme');
  if (saved) {
    document.documentElement.setAttribute('data-bs-theme', saved);
  }

  document.addEventListener('DOMContentLoaded', function() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    var icon = btn.querySelector('i');

    function updateIcon() {
      var current = document.documentElement.getAttribute('data-bs-theme');
      if (icon) {
        icon.className = current === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
      }
    }

    updateIcon();

    btn.addEventListener('click', function() {
      var current = document.documentElement.getAttribute('data-bs-theme');
      var next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-bs-theme', next);
      localStorage.setItem('shekel-theme', next);
      updateIcon();
    });
  });
})();

// Listen for HTMX gridRefresh events to reload the full page.
document.body.addEventListener("gridRefresh", function() {
  window.location.reload();
});

// Reset the Add Transaction form whenever its modal opens.
var addModal = document.getElementById("addTransactionModal");
if (addModal) {
  addModal.addEventListener("show.bs.modal", function() {
    var form = addModal.querySelector("form");
    if (form) form.reset();
  });
}

// Configure HTMX to include CSRF token if we add it later.
document.body.addEventListener("htmx:configRequest", function(event) {
  // Future: event.detail.headers["X-CSRFToken"] = getCsrfToken();
});

// Show a brief loading indicator during HTMX requests.
document.body.addEventListener("htmx:beforeRequest", function(event) {
  const target = event.detail.elt;
  if (target && target.closest && target.closest("td")) {
    target.closest("td").style.opacity = "0.6";
  }
});

document.body.addEventListener("htmx:afterRequest", function(event) {
  const target = event.detail.elt;
  if (target && target.closest && target.closest("td")) {
    target.closest("td").style.opacity = "1";
  }
});
