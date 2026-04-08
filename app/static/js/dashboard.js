/* Shekel Budget App -- Dashboard Interactions
   Handles post-swap cleanup and refresh coordination. */

(function() {
  'use strict';

  /* After a mark-paid swap completes, the bill row transitions to
     its paid state.  No additional JS needed -- HTMX handles the
     swap and the dashboardRefresh trigger causes sections to reload. */
  document.body.addEventListener('htmx:afterSwap', function(evt) {
    var target = evt.detail.target;
    if (target && target.classList && target.classList.contains('bill-row')) {
      /* Bill row was swapped -- no action needed beyond what HTMX
         already handles via HX-Trigger. */
    }
  });
})();
