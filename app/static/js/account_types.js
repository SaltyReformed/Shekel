/**
 * Account types — inline edit styling and save button visibility.
 */
(function() {
  document.querySelectorAll('.account-type-form').forEach(function(form) {
    var display = form.querySelector('.account-type-display');
    var input = form.querySelector('.account-type-input');
    var btn = form.querySelector('.save-type-btn');
    if (!input || !btn || !display) return;
    var original = input.value;

    display.addEventListener('click', function() {
      display.classList.add('d-none');
      input.classList.remove('d-none');
      input.focus();
    });

    input.addEventListener('blur', function() {
      if (!form._changed) {
        input.classList.add('d-none');
        display.classList.remove('d-none');
      }
    });

    input.addEventListener('input', function() {
      form._changed = true;
      btn.classList.toggle('d-none', input.value === original);
    });
  });
})();
