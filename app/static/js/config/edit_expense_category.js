    document.addEventListener('DOMContentLoaded', function () {
        // Update color preview when color input changes
        const colorInput = document.getElementById('color');
        const colorPreview = document.getElementById('colorPreview');

        if (colorInput && colorPreview) {
            colorInput.addEventListener('input', function () {
                colorPreview.style.backgroundColor = this.value;
            });
        }

        // Handle category chip clicks to prefill form
        const categoryChips = document.querySelectorAll('.category-chip');
        const nameInput = document.getElementById('name');

        categoryChips.forEach(chip => {
            chip.addEventListener('click', function () {
                const categoryName = this.dataset.name;
                const categoryColor = this.dataset.color;

                // Set the form values
                if (nameInput) {
                    nameInput.value = categoryName;
                }

                if (colorInput) {
                    colorInput.value = categoryColor;
                    colorPreview.style.backgroundColor = categoryColor;
                }

                // Style chip as selected
                categoryChips.forEach(c => c.classList.remove('selected'));
                this.classList.add('selected');
            });

            // Set chip background color
            chip.style.backgroundColor = chip.dataset.color;
            chip.style.color = 'white';
        });
    });