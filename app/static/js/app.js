/**
 * Shekel Budget App — Client-Side JavaScript
 *
 * Minimal JS: HTMX handles most interactivity server-side.
 * This file provides event listeners and small UX helpers.
 */

// Listen for HTMX gridRefresh events to reload the full page.
document.body.addEventListener("gridRefresh", function() {
  window.location.reload();
});

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
