/**
 * Theme Management Module (static/js/modules/theme.js)
 * ====================================================
 * Manages dark/light theme switching, system preferences sync (#158),
 * and prevents flash of unstyled content (FOUC).
 */

// Early theme resolution to prevent flash of unstyled content (FOUC) (#158)
try {
  const initialTheme = (function () {
    const stored = localStorage.getItem("theme");
    if (stored === "dark" || stored === "light") return stored;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  })();
  document.documentElement.setAttribute("data-bs-theme", initialTheme);
} catch (e) {
  // Graceful fallback
}

/**
 * Resolves preferred color scheme based on stored user choice,
 * defaulting to the system preference (prefers-color-scheme) (#158).
 * @returns {string}
 */
export function getPreferredTheme() {
  const storedTheme = localStorage.getItem("theme");
  if (storedTheme === "dark" || storedTheme === "light") {
    return storedTheme;
  }
  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

/**
 * Sets the active theme on document root and updates all icon toggles.
 * @param {string} theme - 'dark' or 'light'
 */
export function setTheme(theme) {
  document.documentElement.setAttribute("data-bs-theme", theme);
  const toggleBtns = document.querySelectorAll(".theme-toggle-btn");
  toggleBtns.forEach((btn) => updateThemeIcon(btn, theme));
}

/**
 * Updates theme toggle button icon and accessible title.
 * @param {HTMLElement} btn
 * @param {string} theme
 */
export function updateThemeIcon(btn, theme) {
  if (theme === "dark") {
    btn.innerHTML = '<i class="fa-solid fa-sun text-warning"></i>';
    btn.title = "Switch to Light Mode";
  } else {
    btn.innerHTML = '<i class="fa-solid fa-moon text-secondary"></i>';
    btn.title = "Switch to Dark Mode";
  }
}

/**
 * Initializes click listeners on theme toggle buttons and syncs system changes.
 */
export function initThemeToggle() {
  const currentTheme = getPreferredTheme();
  setTheme(currentTheme);

  const toggleBtns = document.querySelectorAll(".theme-toggle-btn");
  toggleBtns.forEach((btn) => {
    if (btn.dataset.themeToggleInitialized === "true") return;
    btn.dataset.themeToggleInitialized = "true";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const activeTheme = document.documentElement.getAttribute("data-bs-theme") || "light";
      const newTheme = activeTheme === "dark" ? "light" : "dark";
      localStorage.setItem("theme", newTheme);
      setTheme(newTheme);
    });
  });

  // Listen to live system preference changes when user hasn't explicitly stored a choice
  if (window.matchMedia) {
    try {
      const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
      const handleSystemChange = (e) => {
        if (!localStorage.getItem("theme")) {
          setTheme(e.matches ? "dark" : "light");
        }
      };
      if (typeof mediaQuery.addEventListener === "function") {
        mediaQuery.addEventListener("change", handleSystemChange);
      } else if (typeof mediaQuery.addListener === "function") {
        mediaQuery.addListener(handleSystemChange);
      }
    } catch (mqErr) {
      console.debug("matchMedia listener not supported:", mqErr);
    }
  }
}

// Auto-initialize when loaded as standalone module
if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initThemeToggle);
  } else {
    initThemeToggle();
  }
}
