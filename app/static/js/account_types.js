/**
 * Account types — inline edit styling and save button visibility.
 */
(function() {
  document.querySelectorAll('.account-type-form').forEach(function(form) {
    var input = form.querySelector('input[name="name"]');
    var btn = form.querySelector('.save-type-btn');
    if (!input || !btn) return;
    var original = input.value;

    input.addEventListener('focus', function() {
      this.classList.remove('bg-transparent', 'border-0');
    });

    input.addEventListener('blur', function() {
      if (!form._changed) {
        this.classList.add('bg-transparent', 'border-0');
      }
    });

    input.addEventListener('input', function() {
      form._changed = true;
      btn.classList.toggle('d-none', input.value === original);
    });
  });
})();
