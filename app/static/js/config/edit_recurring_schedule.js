    document.addEventListener('DOMContentLoaded', function () {
        const typeSelect = document.getElementById('schedule_type');
        const categorySelect = document.getElementById('category_id');

        // Expense categories
        const expenseCategories = [
            {% for category in expense_categories %}
                { id: {{ category.id }}, name: "{{ category.name }}" },
        {% endfor %}
        ];

    // Income categories
    const incomeCategories = [
        {% for category in income_categories %}
    { id: { { category.id } }, name: "{{ category.name }}" },
    {% endfor %}
        ];

    // Function to update categories based on type
    function updateCategories() {
        // Clear existing options
        categorySelect.innerHTML = '<option value="0">-- Select Category --</option>';

        // Get selected type
        const selectedType = typeSelect.options[typeSelect.selectedIndex].text.toLowerCase();

        // Populate with appropriate categories
        if (selectedType === 'expense') {
            expenseCategories.forEach(function (category) {
                const option = document.createElement('option');
                option.value = category.id;
                option.textContent = category.name;

                {% if is_edit and schedule.category_id %}
                if (category.id === {{ schedule.category_id }
            } && "{{ schedule.category_type }}" === "expense") {
                option.selected = true;
            }
            {% endif %}

            categorySelect.appendChild(option);
        });
    } else if (selectedType === 'income') {
        incomeCategories.forEach(function (category) {
            const option = document.createElement('option');
            option.value = category.id;
            option.textContent = category.name;

            {% if is_edit and schedule.category_id %}
            if (category.id === {{ schedule.category_id }
        } && "{{ schedule.category_type }}" === "income") {
            option.selected = true;
        }
        {% endif %}

        categorySelect.appendChild(option);
    });
            }
        }

    // Initial update
    updateCategories();

    // Update on type change
    typeSelect.addEventListener('change', updateCategories);
    });