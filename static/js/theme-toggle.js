(function () {
    const storageKey = "tilestore-theme";
    const root = document.documentElement;

    function preferredTheme() {
        const saved = localStorage.getItem(storageKey);
        return saved || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    }

    function applyTheme(theme) {
        const isDark = theme === "dark";
        root.classList.toggle("dark", isDark);
        root.style.colorScheme = theme;
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            button.setAttribute("aria-pressed", String(isDark));
            if (button.dataset.lightModeLabel) {
                button.setAttribute("aria-label", isDark ? button.dataset.switchToLightLabel : button.dataset.switchToDarkLabel);
                const label = button.querySelector("[data-theme-label]");
                if (label) label.textContent = isDark ? button.dataset.lightModeLabel : button.dataset.darkModeLabel;
            }
            const moon = button.querySelector("[data-theme-moon]");
            const sun = button.querySelector("[data-theme-sun]");
            if (moon && sun) {
                moon.classList.toggle("hidden", isDark);
                sun.classList.toggle("hidden", !isDark);
            }
            const legacyIcon = button.querySelector("[data-theme-icon]");
            if (legacyIcon) legacyIcon.textContent = isDark ? "☀" : "☾";
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        applyTheme(preferredTheme());
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => button.addEventListener("click", () => {
            const next = root.classList.contains("dark") ? "light" : "dark";
            localStorage.setItem(storageKey, next);
            applyTheme(next);
        }));
    });
}());
