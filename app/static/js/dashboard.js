 // Utility function to format currency
    function formatCurrency(value) {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 0,
            maximumFractionDigits: 0
        }).format(value);
    }

    // Utility function to format large numbers
    function formatNumber(value) {
        return new Intl.NumberFormat('en-US').format(value);
    }

    // Update summary cards with data
    function updateSummaryCards(data) {
        document.getElementById('total-balance').textContent = formatNumber(data.total_balance);
        document.getElementById('monthly-income').textContent = formatNumber(data.month_income);
        document.getElementById('monthly-expenses').textContent = formatNumber(data.month_expenses);
        document.getElementById('savings-rate').textContent = data.savings_rate;
        document.getElementById('savings-amount').textContent = formatNumber(data.savings);
    }

    // Create the Income vs Expenses Chart
    function createIncomeExpenseChart(data) {
        const ctx = document.getElementById('income-expense-chart').getContext('2d');
        const labels = data.map(item => item.month);
        const incomeData = data.map(item => item.income);
        const expenseData = data.map(item => item.expenses);

        return new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Income',
                        backgroundColor: 'rgba(0, 184, 148, 0.7)',
                        borderColor: 'rgba(0, 184, 148, 1)',
                        borderWidth: 1,
                        data: incomeData
                    },
                    {
                        label: 'Expenses',
                        backgroundColor: 'rgba(231, 76, 60, 0.7)',
                        borderColor: 'rgba(231, 76, 60, 1)',
                        borderWidth: 1,
                        data: expenseData
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                return context.dataset.label + ': ' + formatCurrency(context.raw);
                            }
                        }
                    },
                    legend: {
                        position: 'top',
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: function (value) {
                                return formatCurrency(value);
                            }
                        }
                    }
                }
            }
        });
    }

    // Create the Balance Trend Chart
    function createBalanceChart(data) {
        const ctx = document.getElementById('balance-chart').getContext('2d');
        const labels = data.map(item => item.month);
        const balanceData = data.map(item => item.balance);

        return new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Net Balance',
                    backgroundColor: 'rgba(53, 99, 230, 0.1)',
                    borderColor: 'rgba(53, 99, 230, 1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    data: balanceData
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                return context.dataset.label + ': ' + formatCurrency(context.raw);
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        ticks: {
                            callback: function (value) {
                                return formatCurrency(value);
                            }
                        }
                    }
                }
            }
        });
    }

    // Create the Expense Categories Chart
    function createExpenseCategoriesChart(data) {
        if (data.length === 0) {
            const container = document.getElementById('expense-categories-chart').parentNode;
            showEmptyState(container, 'No expense data for the current month');
            return null;
        }

        const ctx = document.getElementById('expense-categories-chart').getContext('2d');
        const labels = data.map(item => item.name);
        const values = data.map(item => item.value);
        const colors = data.map(item => item.color);

        return new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: colors,
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                const value = context.raw;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((value / total) * 100).toFixed(1);
                                return context.label + ': ' + formatCurrency(value) + ' (' + percentage + '%)';
                            }
                        }
                    },
                    legend: {
                        position: 'right',
                    }
                }
            }
        });
    }

    // Create the Account Balances Chart
    function createAccountBalancesChart(data) {
        const ctx = document.getElementById('account-balances-chart').getContext('2d');

        // Split into assets and debts
        const assets = data.filter(account => !account.is_debt);
        const debts = data.filter(account => account.is_debt);

        const assetLabels = assets.map(account => account.name);
        const assetValues = assets.map(account => account.balance);
        const assetColors = generateColors(assets.length, 'assets');

        const debtLabels = debts.map(account => account.name);
        const debtValues = debts.map(account => account.balance);
        const debtColors = generateColors(debts.length, 'debts');

        return new Chart(ctx, {
            type: 'bar',
            data: {
                labels: [...assetLabels, ...debtLabels],
                datasets: [{
                    label: 'Balance',
                    backgroundColor: [...assetColors, ...debtColors],
                    borderColor: [...assetColors, ...debtColors].map(color => color.replace('0.7', '1')),
                    borderWidth: 1,
                    data: [...assetValues, ...debtValues.map(value => -value)]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                return formatCurrency(Math.abs(context.raw));
                            },
                            title: function (context) {
                                const index = context[0].dataIndex;
                                const isDebt = index >= assetLabels.length;
                                return (isDebt ? 'Debt: ' : 'Asset: ') + context[0].label;
                            }
                        }
                    },
                    legend: {
                        display: false
                    }
                },
                scales: {
                    x: {
                        ticks: {
                            callback: function (value) {
                                return formatCurrency(Math.abs(value));
                            }
                        }
                    }
                }
            }
        });
    }

    // Create the Expense Breakdown Chart
    function createExpenseBreakdownChart(data) {
        const ctx = document.getElementById('expense-breakdown-chart').getContext('2d');

        // Extract all unique categories
        const allCategories = new Set();
        data.forEach(month => {
            Object.keys(month).forEach(key => {
                if (!['year_month', 'month', 'expenses'].includes(key)) {
                    allCategories.add(key);
                }
            });
        });

        const categoryList = [...allCategories];
        const labels = data.map(item => item.month);

        // Generate a dataset for each category
        const datasets = categoryList.map((category, index) => {
            const color = getCategoryColor(category, index);
            return {
                label: formatCategoryName(category),
                data: data.map(month => month[category] || 0),
                backgroundColor: color,
                borderColor: color.replace('0.7', '1'),
                borderWidth: 1
            };
        });

        return new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: datasets
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                return context.dataset.label + ': ' + formatCurrency(context.raw);
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        stacked: true,
                    },
                    y: {
                        stacked: true,
                        ticks: {
                            callback: function (value) {
                                return formatCurrency(value);
                            }
                        }
                    }
                }
            }
        });
    }

    // Utility function to generate colors for charts
    function generateColors(count, type = 'default') {
        const colors = [];

        if (type === 'assets') {
            // Shades of blue/green for assets
            for (let i = 0; i < count; i++) {
                const hue = 200 - (i * 15) % 60; // Range from blue to green
                colors.push(`rgba(${hue / 3}, ${150 + i * 10}, ${200 - i * 5}, 0.7)`);
            }
        } else if (type === 'debts') {
            // Shades of red/orange for debts
            for (let i = 0; i < count; i++) {
                const hue = 0 + (i * 10) % 60; // Range from red to orange
                colors.push(`rgba(${200 + i * 5}, ${100 + i * 10}, ${50 + i * 5}, 0.7)`);
            }
        } else {
            // Default color palette
            const baseColors = [
                'rgba(53, 99, 230, 0.7)',    // Blue
                'rgba(108, 92, 231, 0.7)',   // Purple
                'rgba(0, 184, 148, 0.7)',    // Green
                'rgba(253, 121, 168, 0.7)',  // Pink
                'rgba(253, 203, 110, 0.7)',  // Yellow
                'rgba(231, 76, 60, 0.7)',    // Red
                'rgba(0, 184, 212, 0.7)',    // Cyan
                'rgba(156, 39, 176, 0.7)',   // Deep Purple
                'rgba(76, 175, 80, 0.7)',    // Light Green
                'rgba(255, 152, 0, 0.7)'     // Orange
            ];

            for (let i = 0; i < count; i++) {
                colors.push(baseColors[i % baseColors.length]);
            }
        }

        return colors;
    }

    // Utility function to format category names
    function formatCategoryName(category) {
        return category.split('_')
            .map(word => word.charAt(0).toUpperCase() + word.slice(1))
            .join(' ');
    }

    // Utility function to get color for a category
    function getCategoryColor(category, index) {
        // Predefined colors for common categories
        const categoryColors = {
            housing: 'rgba(53, 99, 230, 0.7)',
            food: 'rgba(0, 184, 148, 0.7)',
            utilities: 'rgba(108, 92, 231, 0.7)',
            transportation: 'rgba(253, 121, 168, 0.7)',
            entertainment: 'rgba(253, 203, 110, 0.7)',
            health: 'rgba(231, 76, 60, 0.7)',
            education: 'rgba(0, 184, 212, 0.7)',
            shopping: 'rgba(156, 39, 176, 0.7)',
            personal: 'rgba(76, 175, 80, 0.7)',
            other: 'rgba(200, 200, 200, 0.7)'
        };

        return categoryColors[category] || generateColors(1, 'default')[0];
    }

    // Function to show empty state
    function showEmptyState(container, message) {
        // Remove canvas
        const canvas = container.querySelector('canvas');
        if (canvas) {
            canvas.remove();
        }

        // Create empty state
        const emptyState = document.createElement('div');
        emptyState.className = 'empty-state';

        emptyState.innerHTML = `
            <div class="empty-state-icon">
                <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M22 12h-4l-3 9L9 3l-3 9H2"></path>
                </svg>
            </div>
            <div class="empty-state-message">${message}</div>
        `;

        container.appendChild(emptyState);
    }

    // Load all chart data and initialize charts
    document.addEventListener('DOMContentLoaded', function () {
        let incomeExpenseChart, balanceChart, categoriesChart, accountsChart, breakdownChart;

        // Load summary data
        fetch('/dashboard/api/current-summary')
            .then(response => response.json())
            .then(data => {
                updateSummaryCards(data);
            })
            .catch(error => {
                console.error('Error fetching summary data:', error);
            });

        // Load and create Income & Expense Chart
        fetch('/dashboard/api/monthly-finances')
            .then(response => response.json())
            .then(data => {
                incomeExpenseChart = createIncomeExpenseChart(data);
                balanceChart = createBalanceChart(data);
                document.getElementById('income-expense-loading').style.display = 'none';
                document.getElementById('balance-loading').style.display = 'none';
            })
            .catch(error => {
                console.error('Error fetching finance data:', error);
                document.getElementById('income-expense-loading').style.display = 'none';
                document.getElementById('balance-loading').style.display = 'none';

                const incomeExpenseContainer = document.getElementById('income-expense-chart').parentNode;
                const balanceContainer = document.getElementById('balance-chart').parentNode;

                showEmptyState(incomeExpenseContainer, 'Could not load income and expense data');
                showEmptyState(balanceContainer, 'Could not load balance data');
            });

        // Load and create Expense Categories Chart
        fetch('/dashboard/api/expense-categories')
            .then(response => response.json())
            .then(data => {
                categoriesChart = createExpenseCategoriesChart(data);
                document.getElementById('categories-loading').style.display = 'none';
            })
            .catch(error => {
                console.error('Error fetching category data:', error);
                document.getElementById('categories-loading').style.display = 'none';

                const categoriesContainer = document.getElementById('expense-categories-chart').parentNode;
                showEmptyState(categoriesContainer, 'Could not load expense categories');
            });

        // Load and create Account Balances Chart
        fetch('/dashboard/api/account-balances')
            .then(response => response.json())
            .then(data => {
                accountsChart = createAccountBalancesChart(data);
                document.getElementById('accounts-loading').style.display = 'none';
            })
            .catch(error => {
                console.error('Error fetching account data:', error);
                document.getElementById('accounts-loading').style.display = 'none';

                const accountsContainer = document.getElementById('account-balances-chart').parentNode;
                showEmptyState(accountsContainer, 'Could not load account data');
            });

        // Load and create Expense Breakdown Chart
        fetch('/dashboard/api/expense-breakdown')
            .then(response => response.json())
            .then(data => {
                breakdownChart = createExpenseBreakdownChart(data);
                document.getElementById('breakdown-loading').style.display = 'none';
            })
            .catch(error => {
                console.error('Error fetching expense breakdown data:', error);
                document.getElementById('breakdown-loading').style.display = 'none';

                const breakdownContainer = document.getElementById('expense-breakdown-chart').parentNode;
                showEmptyState(breakdownContainer, 'Could not load expense breakdown data');
            });
    });