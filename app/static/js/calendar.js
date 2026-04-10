/* Shekel Budget App -- Calendar Interactions
   Binds day-cell click handlers after HTMX swaps calendar content.
   Clicking a day with transactions shows a detail table below the
   calendar grid; clicking again hides it. */

(function() {
  'use strict';

  document.addEventListener('htmx:afterSettle', function(event) {
    var target = event.detail.target || event.detail.elt;
    if (!target) return;

    /* Only act on swaps into #tab-content (the analytics tab area). */
    var tabContent = document.getElementById('tab-content');
    if (!tabContent || !tabContent.contains(target)) return;

    var detailContainer = tabContent.querySelector('#calendar-day-detail');
    if (!detailContainer) return;

    var activeDay = null;

    tabContent.querySelectorAll('.calendar-day[data-day]').forEach(function(cell) {
      cell.addEventListener('click', function() {
        var day = cell.getAttribute('data-day');
        var template = tabContent.querySelector(
          'template[data-detail-day="' + day + '"]'
        );
        if (!template) return;

        /* Toggle off if clicking the same day. */
        if (activeDay === day) {
          detailContainer.innerHTML = '';
          cell.classList.remove('calendar-day--selected');
          activeDay = null;
          return;
        }

        /* Deselect previous day. */
        if (activeDay !== null) {
          var prev = tabContent.querySelector('.calendar-day--selected');
          if (prev) prev.classList.remove('calendar-day--selected');
        }

        /* Show detail for the clicked day. */
        detailContainer.innerHTML = '';
        detailContainer.appendChild(template.content.cloneNode(true));
        cell.classList.add('calendar-day--selected');
        activeDay = day;

        /* Close button inside the detail section. */
        var closeBtn = detailContainer.querySelector('#calendar-detail-close');
        if (closeBtn) {
          closeBtn.addEventListener('click', function() {
            detailContainer.innerHTML = '';
            cell.classList.remove('calendar-day--selected');
            activeDay = null;
          });
        }
      });
    });
  });
})();
