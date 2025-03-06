document.addEventListener('DOMContentLoaded', function() {
  // Mobile menu toggle
  const mobileMenuToggle = document.querySelector('.mobile-menu-toggle');
  const navContent = document.querySelector('.nav-content');
  const menuOverlay = document.querySelector('.menu-overlay');
  const body = document.body;
  
  if (mobileMenuToggle && navContent) {
    mobileMenuToggle.addEventListener('click', function() {
      mobileMenuToggle.classList.toggle('active');
      navContent.classList.toggle('active');
      if (menuOverlay) menuOverlay.classList.toggle('active');
      
      // Prevent body scrolling when menu is open
      if (navContent.classList.contains('active')) {
        body.style.overflow = 'hidden';
      } else {
        body.style.overflow = '';
      }
    });
    
    // Close menu when clicking overlay
    if (menuOverlay) {
      menuOverlay.addEventListener('click', function() {
        mobileMenuToggle.classList.remove('active');
        navContent.classList.remove('active');
        menuOverlay.classList.remove('active');
        body.style.overflow = '';
      });
    }
  }
  
  // Dropdown functionality for mobile and desktop
  const dropdowns = document.querySelectorAll('.dropdown');
  
  dropdowns.forEach(function(dropdown) {
    const dropbtn = dropdown.querySelector('.dropbtn');
    const dropdownContent = dropdown.querySelector('.dropdown-content');
    
    if (dropbtn && dropdownContent) {
      // Mobile click event
      dropbtn.addEventListener('click', function(e) {
        if (window.innerWidth <= 992) {
          e.preventDefault();
          
          // Close other open dropdowns
          dropdowns.forEach(function(otherDropdown) {
            if (otherDropdown !== dropdown && otherDropdown.classList.contains('open')) {
              otherDropdown.classList.remove('open');
              const otherBtn = otherDropdown.querySelector('.dropbtn');
              if (otherBtn) otherBtn.setAttribute('aria-expanded', 'false');
            }
          });
          
          // Toggle current dropdown
          dropdown.classList.toggle('open');
          const isExpanded = dropdown.classList.contains('open');
          dropbtn.setAttribute('aria-expanded', isExpanded);
        }
      });
      
      // For keyboard accessibility
      dropbtn.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          dropdown.classList.toggle('open');
          const isExpanded = dropdown.classList.contains('open');
          dropbtn.setAttribute('aria-expanded', isExpanded);
        }
      });
    }
  });
  
  // User dropdown functionality
  const userDropdown = document.querySelector('.user-dropdown');
  const userButton = document.querySelector('.user-button');
  
  if (userButton && userDropdown) {
    userButton.addEventListener('click', function(e) {
      if (window.innerWidth <= 992) {
        e.preventDefault();
        userDropdown.classList.toggle('open');
        const isExpanded = userDropdown.classList.contains('open');
        userButton.setAttribute('aria-expanded', isExpanded);
      }
    });
    
    // Close dropdown when clicking outside
    document.addEventListener('click', function(e) {
      if (!userDropdown.contains(e.target) && window.innerWidth > 992) {
        userDropdown.classList.remove('open');
        userButton.setAttribute('aria-expanded', 'false');
      }
    });
  }
  
  // Smooth dropdown animations for desktop
  if (window.innerWidth > 992) {
    dropdowns.forEach(function(dropdown) {
      let hoverTimeout;
      
      dropdown.addEventListener('mouseenter', function() {
        clearTimeout(hoverTimeout);
        dropdowns.forEach(function(d) {
          if (d !== dropdown) {
            d.classList.remove('open');
          }
        });
        dropdown.classList.add('open');
      });
      
      dropdown.addEventListener('mouseleave', function() {
        hoverTimeout = setTimeout(function() {
          dropdown.classList.remove('open');
        }, 100);
      });
    });
    
    // Same for user dropdown
    if (userDropdown) {
      let userTimeout;
      
      userDropdown.addEventListener('mouseenter', function() {
        clearTimeout(userTimeout);
        userDropdown.classList.add('open');
      });
      
      userDropdown.addEventListener('mouseleave', function() {
        userTimeout = setTimeout(function() {
          userDropdown.classList.remove('open');
        }, 100);
      });
    }
  }
  
  // Add active class to current page link
  const currentPath = window.location.pathname;
  const navLinks = document.querySelectorAll('.nav-link');
  
  navLinks.forEach(link => {
    const linkPath = link.getAttribute('href');
    if (linkPath && currentPath === linkPath) {
      link.classList.add('active');
    } else if (linkPath && currentPath.startsWith(linkPath) && linkPath !== '/') {
      // For sections like /account/*, /income/*, etc.
      link.classList.add('active');
    }
  });
  
  // Scroll effect for navbar
  let lastScroll = 0;
  const navbar = document.querySelector('.navbar');
  
  window.addEventListener('scroll', () => {
    const currentScroll = window.pageYOffset;
    
    if (currentScroll <= 0) {
      navbar.classList.remove('scrolled-down');
      navbar.classList.remove('scrolled-up');
      return;
    }
    
    if (currentScroll > lastScroll && !navbar.classList.contains('scrolled-down')) {
      // Scroll Down
      navbar.classList.remove('scrolled-up');
      navbar.classList.add('scrolled-down');
    } else if (currentScroll < lastScroll && navbar.classList.contains('scrolled-down')) {
      // Scroll Up
      navbar.classList.remove('scrolled-down');
      navbar.classList.add('scrolled-up');
    }
    
    lastScroll = currentScroll;
  });
});