/* Shekel Budget App -- Calendar Interactions
   Initializes Bootstrap popovers after HTMX swaps calendar content. */

(function() {
  'use strict';

  /* Dispose existing popovers within a container before re-init. */
  function disposePopovers(container) {
    container.querySelectorAll('[data-bs-toggle="popover"]').forEach(function(el) {
      var instance = bootstrap.Popover.getInstance(el);
      if (instance) {
        instance.dispose();
      }
    });
  }

  /* Initialize popovers on new calendar content after HTMX swap. */
  document.addEventListener('htmx:afterSettle', function(event) {
    var target = event.detail.target || event.detail.elt;
    if (!target) return;

    /* Only act on swaps into #tab-content (the analytics tab area). */
    var tabContent = document.getElementById('tab-content');
    if (!tabContent || !tabContent.contains(target)) return;

    disposePopovers(tabContent);

    tabContent.querySelectorAll('[data-bs-toggle="popover"]').forEach(function(el) {
      new bootstrap.Popover(el);
    });
  });
})();
