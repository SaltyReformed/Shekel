document.addEventListener('DOMContentLoaded', function() {
    // Color picker functionality
    const colorInput = document.getElementById('color');
    const colorPreview = document.getElementById('colorPreview');
    const iconPreview = document.getElementById('iconPreview');
    const iconInput = document.getElementById('icon');
    const iconOptions = document.querySelectorAll('.icon-option');
    
    // Update color preview when color changes
    if (colorInput && colorPreview) {
        colorInput.addEventListener('input', function() {
            colorPreview.style.backgroundColor = this.value;
            
            // Also update icon preview background
            if (iconPreview) {
                iconPreview.style.backgroundColor = this.value;
            }
        });
    }
    
    // Handle icon field updates
    if (iconInput && iconPreview) {
        iconInput.addEventListener('input', updateIconPreview);
        
        // Initial call to ensure preview matches current value
        updateIconPreview();
    }
    
    // Handle category chip selection
    const categoryChips = document.querySelectorAll('.category-chip');
    if (categoryChips) {
        categoryChips.forEach(chip => {
            chip.addEventListener('click', function() {
                const nameInput = document.getElementById('name');
                const chipName = this.getAttribute('data-name');
                const chipColor = this.getAttribute('data-color');
                
                if (nameInput && chipName) {
                    nameInput.value = chipName;
                }
                
                if (colorInput && colorPreview && chipColor) {
                    colorInput.value = chipColor;
                    colorPreview.style.backgroundColor = chipColor;
                    iconPreview.style.backgroundColor = chipColor;
                }
            });
        });
    }
    
    // Handle icon selection from common icons
    if (iconOptions) {
        iconOptions.forEach(option => {
            option.addEventListener('click', function() {
                const iconPath = this.getAttribute('data-path');
                
                // Clear previous selection
                iconOptions.forEach(opt => opt.classList.remove('selected'));
                
                // Mark this option as selected
                this.classList.add('selected');
                
                // Update the icon input field
                if (iconInput && iconPath) {
                    iconInput.value = iconPath;
                    updateIconPreview();
                }
            });
        });
    }
    
    // Function to update the icon preview
    function updateIconPreview() {
        const iconValue = iconInput.value.trim();
        
        // Clear previous content
        iconPreview.innerHTML = '';
        
        if (iconValue) {
            // Create SVG element with the path data
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
            svg.setAttribute('width', '24');
            svg.setAttribute('height', '24');
            svg.setAttribute('viewBox', '0 0 24 24');
            svg.setAttribute('fill', 'none');
            svg.setAttribute('stroke', 'currentColor');
            svg.setAttribute('stroke-width', '2');
            svg.setAttribute('stroke-linecap', 'round');
            svg.setAttribute('stroke-linejoin', 'round');
            
            // Create path element and add data
            const paths = iconValue.split(/\s(?=[A-Z])/g); // Split paths by space followed by uppercase letter
            
            paths.forEach(pathData => {
                const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                path.setAttribute('d', pathData);
                svg.appendChild(path);
            });
            
            iconPreview.appendChild(svg);
            
            // Highlight the matching icon option if exists
            highlightMatchingIcon(iconValue);
        } else {
            // Show default text if no icon
            const span = document.createElement('span');
            span.textContent = 'Icon';
            iconPreview.appendChild(span);
        }
    }
    
    // Function to highlight the matching icon in the grid
    function highlightMatchingIcon(iconPath) {
        if (!iconOptions) return;
        
        // Remove selection from all icons
        iconOptions.forEach(option => {
            option.classList.remove('selected');
            
            // Check if this option matches the current path
            if (option.getAttribute('data-path') === iconPath) {
                option.classList.add('selected');
            }
        });
    }
});