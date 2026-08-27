document.addEventListener("DOMContentLoaded", () => {
    const menu = document.getElementById("dashboard-mobile-menu");
    const overlay = document.getElementById("dashboard-mobile-overlay");
    const toggle = document.querySelector("[data-dashboard-menu-toggle]");

    if (!menu || !overlay || !toggle) return;

    const setOpen = (isOpen) => {
        menu.classList.toggle("hidden", !isOpen);
        overlay.classList.toggle("hidden", !isOpen);
        overlay.setAttribute("aria-hidden", String(!isOpen));
        toggle.setAttribute("aria-expanded", String(isOpen));
        if (isOpen) {
            menu.focus();
        } else {
            toggle.focus();
        }
    };

    toggle.addEventListener("click", () => setOpen(menu.classList.contains("hidden")));
    overlay.addEventListener("click", () => setOpen(false));
    menu.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => setOpen(false)));
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") setOpen(false);
    });
});
